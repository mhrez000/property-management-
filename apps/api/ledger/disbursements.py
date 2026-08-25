"""Owner allocation and month-end disbursement runs.

Allocation moves receipted funds from tenancy sub-ledgers to owner
sub-ledgers, splitting across ownership shares (basis points, integer math,
remainder to the first owner) and deducting the property's management fee in
the same balanced entry.

Disbursement runs then pay owners out of trust behind a human approval gate
(see :class:`ledger.models.DisbursementRun`). Everything is integer cents;
every movement is a balanced, immutable journal entry.
"""

from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ledger.models import DisbursementLine, DisbursementRun, JournalEntry, TrustAccount
from ledger.services import (
    account_balance_cents,
    credit,
    debit,
    disburse_to_owner,
    ensure_agency_fees_account,
    ensure_owner_account,
    ensure_tenancy_account,
    post_entry,
)
from portfolio.models import Owner, Tenancy


def split_by_shares(total_cents: int, shares_bps: list[int]) -> list[int]:
    """Split an amount across share weights without losing a cent.

    Each portion is floored; the remainder goes to the first share (the
    conventional treatment for a primary owner).
    """
    total_shares = sum(shares_bps)
    if total_shares <= 0:
        raise ValidationError("Ownership shares must sum to a positive amount.")
    portions = [total_cents * share // total_shares for share in shares_bps]
    portions[0] += total_cents - sum(portions)
    return portions


@transaction.atomic
def allocate_tenancy_funds(tenancy: Tenancy, *, created_by=None) -> list[JournalEntry]:
    """Move a tenancy sub-ledger's balance to its owners, net of fees.

    One balanced entry per owner:
        DR tenancy ledger (gross share)
        CR owner ledger  (gross − fee)
        CR agency fees   (fee)

    Idempotent by construction: allocating twice in a row is a no-op the
    second time because the tenancy balance is already zero.
    """
    tenancy_ledger = ensure_tenancy_account(tenancy)
    balance = account_balance_cents(tenancy_ledger)
    if balance <= 0:
        return []

    ownerships = list(
        tenancy.property.ownerships.select_related("owner").order_by("created_at")
    )
    if not ownerships:
        raise ValidationError(
            f"Property for tenancy {tenancy.reference} has no owners; cannot allocate funds."
        )

    fee_bps = tenancy.property.management_fee_bps
    portions = split_by_shares(balance, [o.share_basis_points for o in ownerships])

    entries = []
    for ownership, gross in zip(ownerships, portions):
        if gross <= 0:
            continue
        fee = gross * fee_bps // 10000
        owner_ledger = ensure_owner_account(ownership.owner)
        lines = [debit(tenancy_ledger, gross), credit(owner_ledger, gross - fee)]
        if fee > 0:
            lines.append(credit(ensure_agency_fees_account(tenancy.organisation_id), fee))
        entries.append(
            post_entry(
                organisation_id=tenancy.organisation_id,
                description=(
                    f"Rent allocation — {tenancy.reference} → {ownership.owner.name}"
                    + (f" (fee {fee_bps / 100:.2f}%)" if fee else "")
                ),
                lines=lines,
                created_by=created_by,
            )
        )
    return entries


@transaction.atomic
def create_disbursement_run(
    *, trust_account: TrustAccount, period_end: date, created_by=None
) -> DisbursementRun:
    """Draft a run by snapshotting every positive owner ledger balance."""
    run = DisbursementRun.objects.create(
        organisation_id=trust_account.organisation_id,
        trust_account=trust_account,
        period_end=period_end,
        created_by=created_by,
    )
    owners = Owner.objects.filter(organisation_id=trust_account.organisation_id)
    for owner in owners:
        balance = account_balance_cents(ensure_owner_account(owner))
        if balance > 0:
            DisbursementLine.objects.create(
                organisation_id=run.organisation_id,
                run=run,
                owner=owner,
                amount_cents=balance,
            )
    return run


@transaction.atomic
def approve_disbursement_run(run: DisbursementRun, *, approved_by) -> DisbursementRun:
    if not run.lines.exists():
        raise ValidationError("Cannot approve an empty disbursement run.")
    run.transition_to(DisbursementRun.State.APPROVED)
    run.approved_by = approved_by
    run.save(update_fields=["state", "approved_by", "updated_at"])
    return run


@transaction.atomic
def execute_disbursement_run(run: DisbursementRun, *, executed_by=None) -> DisbursementRun:
    """Pay every line out of trust. Idempotent and crash-safe:

    - each payout's journal entry is keyed on the line id, so a retried
      execution reuses the original entry rather than double-paying;
    - a line whose owner balance has shrunk below the snapshot (e.g. a fee
      posted after drafting) is SKIPPED, never partially paid — the next
      run picks the balance up.
    """
    if run.state != DisbursementRun.State.APPROVED:
        raise ValidationError("Only an approved run can be executed.")

    for line in run.lines.select_for_update().filter(status=DisbursementLine.Status.PENDING):
        owner_balance = account_balance_cents(ensure_owner_account(line.owner))
        if owner_balance < line.amount_cents:
            line.status = DisbursementLine.Status.SKIPPED
            line.save(update_fields=["status", "updated_at"])
            continue
        entry = disburse_to_owner(
            owner=line.owner,
            trust_account=run.trust_account,
            amount_cents=line.amount_cents,
            external_id=f"disbursement:{run.pk}:{line.pk}",
            description=f"Owner disbursement run {run.period_end.isoformat()} — {line.owner.name}",
            created_by=executed_by,
        )
        line.entry = entry
        line.status = DisbursementLine.Status.PAID
        line.save(update_fields=["entry", "status", "updated_at"])

    run.transition_to(DisbursementRun.State.EXECUTED)
    run.executed_at = timezone.now()
    run.save(update_fields=["state", "executed_at", "updated_at"])
    return run
