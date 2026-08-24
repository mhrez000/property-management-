from datetime import date

import pytest
from django.core.exceptions import ValidationError

from common.tenancy import org_context
from portfolio.models import Lease, RentCharge, RentSchedule
from portfolio.services import (
    apply_receipt_to_charges,
    arrears_for_tenancy,
    generate_rent_charges,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def lease(org, tenancy):
    with org_context(org.pk):
        yield Lease.objects.create(
            organisation=org,
            tenancy=tenancy,
            state=Lease.State.ACTIVE,
            start_date=date(2026, 1, 1),
            rent_amount_cents=65_000,
            rent_frequency="weekly",
            bond_amount_cents=260_000,
        )


def _schedule(org, lease, frequency, first_due, amount=65_000):
    return RentSchedule.objects.create(
        organisation=org,
        lease=lease,
        amount_cents=amount,
        frequency=frequency,
        first_due_date=first_due,
    )


class TestRentChargeGeneration:
    def test_weekly_charges(self, org, lease):
        with org_context(org.pk):
            schedule = _schedule(org, lease, "weekly", date(2026, 1, 1))
            charges = generate_rent_charges(schedule, until=date(2026, 1, 31))
            assert [c.due_date for c in charges] == [
                date(2026, 1, 1),
                date(2026, 1, 8),
                date(2026, 1, 15),
                date(2026, 1, 22),
                date(2026, 1, 29),
            ]
            first = charges[0]
            assert (first.period_start, first.period_end) == (date(2026, 1, 1), date(2026, 1, 7))

    def test_monthly_charges_clamp_short_months(self, org, lease):
        with org_context(org.pk):
            schedule = _schedule(org, lease, "monthly", date(2026, 1, 31))
            charges = generate_rent_charges(schedule, until=date(2026, 4, 30))
            assert [c.due_date for c in charges] == [
                date(2026, 1, 31),
                date(2026, 2, 28),
                date(2026, 3, 31),
                date(2026, 4, 30),
            ]

    def test_generation_is_idempotent(self, org, lease):
        with org_context(org.pk):
            schedule = _schedule(org, lease, "fortnightly", date(2026, 1, 1))
            first_run = generate_rent_charges(schedule, until=date(2026, 2, 1))
            second_run = generate_rent_charges(schedule, until=date(2026, 3, 1))
            assert len(first_run) == 3
            # Only the new horizon's charges are created on re-run.
            assert all(c.due_date > date(2026, 2, 1) for c in second_run)
            assert schedule.charges.count() == 5

    def test_schedule_end_date_caps_generation(self, org, lease):
        with org_context(org.pk):
            schedule = _schedule(org, lease, "weekly", date(2026, 1, 1))
            schedule.end_date = date(2026, 1, 10)
            schedule.save()
            charges = generate_rent_charges(schedule, until=date(2026, 3, 1))
            assert [c.due_date for c in charges] == [date(2026, 1, 1), date(2026, 1, 8)]


class TestReceiptApplication:
    def test_oldest_first_with_partial_and_credit(self, org, lease, tenancy):
        with org_context(org.pk):
            schedule = _schedule(org, lease, "weekly", date(2026, 1, 1))
            generate_rent_charges(schedule, until=date(2026, 1, 15))  # 3 charges of 650.00

            remainder = apply_receipt_to_charges(tenancy.pk, 100_000)
            assert remainder == 0
            charges = list(RentCharge.objects.order_by("due_date"))
            assert charges[0].status == RentCharge.Status.PAID
            assert charges[1].status == RentCharge.Status.PARTIALLY_PAID
            assert charges[1].paid_cents == 35_000
            assert charges[2].status == RentCharge.Status.DUE

            remainder = apply_receipt_to_charges(tenancy.pk, 200_000)
            assert remainder == 200_000 - 30_000 - 65_000
            assert all(
                c.status == RentCharge.Status.PAID for c in RentCharge.objects.all()
            )

    def test_arrears_summary(self, org, lease, tenancy):
        with org_context(org.pk):
            schedule = _schedule(org, lease, "weekly", date(2026, 1, 1))
            generate_rent_charges(schedule, until=date(2026, 1, 15))
            apply_receipt_to_charges(tenancy.pk, 65_000)
            summary = arrears_for_tenancy(tenancy.pk, as_of=date(2026, 1, 20))
            assert summary["total_owing_cents"] == 130_000
            assert summary["days_overdue"] == 12  # oldest unpaid due 8 Jan
            assert summary["unpaid_charges"] == 2


class TestLeaseStateMachine:
    def test_valid_transition(self, org, lease):
        with org_context(org.pk):
            lease.transition_to(Lease.State.ENDING)
            lease.refresh_from_db()
            assert lease.state == Lease.State.ENDING

    def test_invalid_transition_raises(self, org, lease):
        with org_context(org.pk):
            with pytest.raises(ValidationError, match="Invalid lease transition"):
                lease.transition_to(Lease.State.DRAFT)
