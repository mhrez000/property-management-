"""Rent schedule services: generating rent charges and applying receipts."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from django.db import transaction

from portfolio.models import RentCharge, RentSchedule


def _add_months(d: date, months: int) -> date:
    """Advance by calendar months, clamping to the last day of short months
    (e.g. 31 Jan + 1 month = 28/29 Feb)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _due_for(first_due: date, n: int, frequency: str) -> date:
    """The nth due date, always computed from the schedule's anchor date.

    Monthly dates are anchored to ``first_due``'s day-of-month so a clamped
    February (28th) does not drift every later month to the 28th.
    """
    if frequency == RentSchedule.Frequency.WEEKLY:
        return first_due + timedelta(days=7 * n)
    if frequency == RentSchedule.Frequency.FORTNIGHTLY:
        return first_due + timedelta(days=14 * n)
    if frequency == RentSchedule.Frequency.MONTHLY:
        return _add_months(first_due, n)
    raise ValueError(f"Unknown rent frequency: {frequency}")


@transaction.atomic
def generate_rent_charges(schedule: RentSchedule, until: date) -> list[RentCharge]:
    """Materialise rent charges from the schedule up to ``until`` (inclusive).

    Idempotent: re-running never duplicates a charge (unique on
    (schedule, due_date)); only missing charges are created. Intended to be
    run periodically by Celery beat with a rolling horizon.
    """
    existing = set(schedule.charges.values_list("due_date", flat=True))
    created: list[RentCharge] = []
    n = 0
    while True:
        due = _due_for(schedule.first_due_date, n, schedule.frequency)
        if due > until or (schedule.end_date and due > schedule.end_date):
            break
        if due not in existing:
            # Rent is paid in advance: the charge covers up to the day before
            # the next occurrence.
            period_end = _due_for(schedule.first_due_date, n + 1, schedule.frequency) - timedelta(
                days=1
            )
            created.append(
                RentCharge.objects.create(
                    organisation_id=schedule.organisation_id,
                    schedule=schedule,
                    tenancy_id=schedule.lease.tenancy_id,
                    due_date=due,
                    period_start=due,
                    period_end=period_end,
                    amount_cents=schedule.amount_cents,
                )
            )
        n += 1
    return created


@transaction.atomic
def apply_receipt_to_charges(tenancy_id, amount_cents: int) -> int:
    """Settle a received amount against the tenancy's unpaid charges,
    oldest first. Returns any unallocated remainder (credit in advance).

    Bookkeeping only — the authoritative money record is the ledger entry;
    this keeps arrears views cheap to query.
    """
    remaining = amount_cents
    charges = (
        RentCharge.objects.select_for_update()
        .filter(tenancy_id=tenancy_id)
        .exclude(status=RentCharge.Status.PAID)
        .order_by("due_date")
    )
    for charge in charges:
        if remaining <= 0:
            break
        owing = charge.amount_cents - charge.paid_cents
        applied = min(owing, remaining)
        charge.paid_cents += applied
        charge.status = (
            RentCharge.Status.PAID
            if charge.paid_cents == charge.amount_cents
            else RentCharge.Status.PARTIALLY_PAID
        )
        charge.save(update_fields=["paid_cents", "status", "updated_at"])
        remaining -= applied
    return remaining


def arrears_for_tenancy(tenancy_id, as_of: date) -> dict:
    """Simple arrears summary for a tenancy: total owing and days overdue."""
    unpaid = (
        RentCharge.objects.filter(tenancy_id=tenancy_id, due_date__lte=as_of)
        .exclude(status=RentCharge.Status.PAID)
        .order_by("due_date")
    )
    total_owing = sum(c.amount_cents - c.paid_cents for c in unpaid)
    oldest = unpaid.first()
    return {
        "total_owing_cents": total_owing,
        "days_overdue": (as_of - oldest.due_date).days if oldest else 0,
        "unpaid_charges": unpaid.count(),
    }
