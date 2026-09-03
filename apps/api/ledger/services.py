"""Ledger services — the only sanctioned write path into the ledger.

Application code must never create JournalEntry/Posting rows directly;
everything goes through :func:`post_entry` so validation, idempotency and
atomicity hold in one place (with the database triggers as the final
backstop).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from ledger.models import (
    Account,
    BankTransaction,
    JournalEntry,
    Posting,
    Receipt,
    ReceiptSequence,
    Reconciliation,
    TrustAccount,
)


@dataclass(frozen=True)
class Line:
    account_id: object  # Account pk (UUID)
    side: str  # Posting.Side
    amount_cents: int


def debit(account, amount_cents: int) -> Line:
    return Line(account_id=account.pk, side=Posting.Side.DEBIT, amount_cents=amount_cents)


def credit(account, amount_cents: int) -> Line:
    return Line(account_id=account.pk, side=Posting.Side.CREDIT, amount_cents=amount_cents)


@transaction.atomic
def post_entry(
    *,
    organisation_id,
    description: str,
    lines: list[Line],
    external_id: str | None = None,
    posted_at: datetime | None = None,
    created_by=None,
    reverses: JournalEntry | None = None,
) -> JournalEntry:
    """Atomically post a balanced journal entry.

    Idempotent on ``external_id``: if an entry with the same external id
    already exists for the organisation, it is returned unchanged.
    """
    if len(lines) < 2:
        raise ValidationError("A journal entry requires at least two postings.")
    debits = sum(l.amount_cents for l in lines if l.side == Posting.Side.DEBIT)
    credits = sum(l.amount_cents for l in lines if l.side == Posting.Side.CREDIT)
    if debits != credits:
        raise ValidationError(f"Entry does not balance: debits {debits} != credits {credits}.")
    if any(
        isinstance(l.amount_cents, bool) or not isinstance(l.amount_cents, int) or l.amount_cents <= 0
        for l in lines
    ):
        raise ValidationError("Posting amounts must be positive integers of cents.")

    if external_id is not None:
        existing = JournalEntry.objects.filter(
            organisation_id=organisation_id, external_id=external_id
        ).first()
        if existing is not None:
            return existing

    entry = JournalEntry.objects.create(
        organisation_id=organisation_id,
        description=description,
        posted_at=posted_at or timezone.now(),
        external_id=external_id,
        created_by=created_by,
        reverses=reverses,
    )
    Posting.objects.bulk_create(
        [
            Posting(
                organisation_id=organisation_id,
                journal_entry=entry,
                account_id=line.account_id,
                side=line.side,
                amount_cents=line.amount_cents,
            )
            for line in lines
        ]
    )
    return entry


@transaction.atomic
def reverse_entry(entry: JournalEntry, *, description: str | None = None, created_by=None) -> JournalEntry:
    """Correct an entry by posting its mirror image. The original is untouched."""
    if hasattr(entry, "reversed_by"):
        raise ValidationError("Entry has already been reversed.")
    flipped = {Posting.Side.DEBIT: Posting.Side.CREDIT, Posting.Side.CREDIT: Posting.Side.DEBIT}
    lines = [
        Line(account_id=p.account_id, side=flipped[Posting.Side(p.side)], amount_cents=p.amount_cents)
        for p in entry.postings.all()
    ]
    return post_entry(
        organisation_id=entry.organisation_id,
        description=description or f"Reversal of: {entry.description}",
        lines=lines,
        created_by=created_by,
        reverses=entry,
    )


def next_receipt_number(trust_account: TrustAccount) -> int:
    """Allocate the next gap-free receipt number.

    Must be called inside the same transaction that creates the receipt: the
    row lock serialises allocation, and a rollback returns the number.
    """
    seq = (
        ReceiptSequence.objects.select_for_update()
        .filter(trust_account=trust_account)
        .first()
    )
    if seq is None:
        seq = ReceiptSequence.objects.create(
            organisation_id=trust_account.organisation_id, trust_account=trust_account
        )
        seq = ReceiptSequence.objects.select_for_update().get(pk=seq.pk)
    number = seq.next_number
    seq.next_number += 1
    seq.save(update_fields=["next_number", "updated_at"])
    return number


def account_balance_cents(account: Account, *, as_of: date | None = None) -> int:
    """Balance derived from postings, signed per the account's normal side."""
    qs = account.postings.all()
    if as_of is not None:
        qs = qs.filter(journal_entry__posted_at__date__lte=as_of)
    sums = qs.aggregate(
        debits=models.Sum("amount_cents", filter=models.Q(side=Posting.Side.DEBIT), default=0),
        credits=models.Sum("amount_cents", filter=models.Q(side=Posting.Side.CREDIT), default=0),
    )
    raw = sums["debits"] - sums["credits"]
    return raw if account.is_debit_normal else -raw


def trial_balance_cents(organisation_id, *, as_of: date | None = None) -> int:
    """Σ(debits) − Σ(credits) across the whole org — must always be zero.

    A nonzero result means something bypassed the balancing trigger: treat it
    as a data-integrity incident.
    """
    qs = Posting.objects.filter(organisation_id=organisation_id)
    if as_of is not None:
        qs = qs.filter(journal_entry__posted_at__date__lte=as_of)
    sums = qs.aggregate(
        debits=models.Sum("amount_cents", filter=models.Q(side=Posting.Side.DEBIT), default=0),
        credits=models.Sum("amount_cents", filter=models.Q(side=Posting.Side.CREDIT), default=0),
    )
    return sums["debits"] - sums["credits"]


# ---------------------------------------------------------------------------
# Account provisioning
# ---------------------------------------------------------------------------

def ensure_trust_bank_account(trust_account: TrustAccount) -> Account:
    account, _ = Account.objects.get_or_create(
        organisation_id=trust_account.organisation_id,
        trust_account=trust_account,
        subtype=Account.Subtype.TRUST_BANK,
        defaults={"name": f"Trust bank — {trust_account.name}", "type": Account.Type.ASSET},
    )
    return account


def ensure_tenancy_account(tenancy) -> Account:
    account, _ = Account.objects.get_or_create(
        organisation_id=tenancy.organisation_id,
        tenancy=tenancy,
        defaults={
            "name": f"Tenancy ledger — {tenancy.reference}",
            "type": Account.Type.LIABILITY,
            "subtype": Account.Subtype.TENANCY_LEDGER,
        },
    )
    return account


def ensure_owner_account(owner) -> Account:
    account, _ = Account.objects.get_or_create(
        organisation_id=owner.organisation_id,
        owner=owner,
        defaults={
            "name": f"Owner ledger — {owner.name}",
            "type": Account.Type.LIABILITY,
            "subtype": Account.Subtype.OWNER_LEDGER,
        },
    )
    return account


def ensure_bond_clearing_account(organisation_id) -> Account:
    account, _ = Account.objects.get_or_create(
        organisation_id=organisation_id,
        subtype=Account.Subtype.BOND_CLEARING,
        defaults={"name": "Bond clearing", "type": Account.Type.LIABILITY},
    )
    return account


def ensure_agency_fees_account(organisation_id) -> Account:
    account, _ = Account.objects.get_or_create(
        organisation_id=organisation_id,
        subtype=Account.Subtype.AGENCY_FEES,
        defaults={"name": "Agency fees clearing", "type": Account.Type.LIABILITY},
    )
    return account


# ---------------------------------------------------------------------------
# Trust workflows
# ---------------------------------------------------------------------------

@transaction.atomic
def receipt_rent(
    *,
    tenancy,
    trust_account: TrustAccount,
    amount_cents: int,
    received_from: str,
    external_id: str,
    method: str = "eft",
    posted_at: datetime | None = None,
    created_by=None,
) -> Receipt:
    """Receive rent into trust: DR trust bank / CR tenancy sub-ledger, with a
    statutory receipt. Idempotent on ``external_id`` — a retried webhook
    returns the original receipt rather than double-receipting."""
    existing = JournalEntry.objects.filter(
        organisation_id=tenancy.organisation_id, external_id=external_id
    ).first()
    if existing is not None:
        return existing.receipt

    bank = ensure_trust_bank_account(trust_account)
    tenancy_ledger = ensure_tenancy_account(tenancy)
    when = posted_at or timezone.now()
    entry = post_entry(
        organisation_id=tenancy.organisation_id,
        description=f"Rent received — {tenancy.reference}",
        lines=[debit(bank, amount_cents), credit(tenancy_ledger, amount_cents)],
        external_id=external_id,
        posted_at=when,
        created_by=created_by,
    )
    return Receipt.objects.create(
        organisation_id=tenancy.organisation_id,
        trust_account=trust_account,
        number=next_receipt_number(trust_account),
        journal_entry=entry,
        amount_cents=amount_cents,
        received_from=received_from,
        method=method,
        issued_at=when,
    )


@transaction.atomic
def allocate_rent_to_owner(
    *, tenancy, owner, amount_cents: int, description: str | None = None, created_by=None
) -> JournalEntry:
    """Move receipted rent from the tenancy sub-ledger to the owner's:
    DR tenancy ledger / CR owner ledger."""
    tenancy_ledger = ensure_tenancy_account(tenancy)
    owner_ledger = ensure_owner_account(owner)
    return post_entry(
        organisation_id=tenancy.organisation_id,
        description=description or f"Rent allocated to owner — {tenancy.reference}",
        lines=[debit(tenancy_ledger, amount_cents), credit(owner_ledger, amount_cents)],
        created_by=created_by,
    )


@transaction.atomic
def charge_management_fee(
    *, owner, amount_cents: int, description: str | None = None, created_by=None
) -> JournalEntry:
    """DR owner ledger / CR agency fees clearing."""
    owner_ledger = ensure_owner_account(owner)
    fees = ensure_agency_fees_account(owner.organisation_id)
    return post_entry(
        organisation_id=owner.organisation_id,
        description=description or f"Management fee — {owner.name}",
        lines=[debit(owner_ledger, amount_cents), credit(fees, amount_cents)],
        created_by=created_by,
    )


@transaction.atomic
def disburse_to_owner(
    *,
    owner,
    trust_account: TrustAccount,
    amount_cents: int,
    external_id: str | None = None,
    description: str | None = None,
    created_by=None,
) -> JournalEntry:
    """Pay the owner out of trust: DR owner ledger / CR trust bank."""
    owner_ledger = ensure_owner_account(owner)
    bank = ensure_trust_bank_account(trust_account)
    balance = account_balance_cents(owner_ledger)
    if amount_cents > balance:
        raise ValidationError(
            f"Disbursement {amount_cents} exceeds owner ledger balance {balance}: "
            "a trust sub-ledger must never go into deficit."
        )
    return post_entry(
        organisation_id=owner.organisation_id,
        description=description or f"Owner disbursement — {owner.name}",
        lines=[debit(owner_ledger, amount_cents), credit(bank, amount_cents)],
        external_id=external_id,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@transaction.atomic
def run_three_way_reconciliation(
    *, trust_account: TrustAccount, cutoff_date: date, prepared_by=None
) -> Reconciliation:
    """Bank = cashbook = Σ sub-ledgers, to the cent, at the cut-off date."""
    bank_balance = (
        BankTransaction.objects.filter(trust_account=trust_account, date__lte=cutoff_date).aggregate(
            total=models.Sum("amount_cents", default=0)
        )["total"]
    )
    cashbook_balance = account_balance_cents(
        ensure_trust_bank_account(trust_account), as_of=cutoff_date
    )
    subledger_accounts = Account.objects.filter(
        organisation_id=trust_account.organisation_id,
        subtype__in=[
            Account.Subtype.TENANCY_LEDGER,
            Account.Subtype.OWNER_LEDGER,
            Account.Subtype.AGENCY_FEES,
            Account.Subtype.BOND_CLEARING,
        ],
    )
    subledger_total = sum(account_balance_cents(a, as_of=cutoff_date) for a in subledger_accounts)

    status = (
        Reconciliation.Status.BALANCED
        if bank_balance == cashbook_balance == subledger_total
        else Reconciliation.Status.OUT_OF_BALANCE
    )
    return Reconciliation.objects.create(
        organisation_id=trust_account.organisation_id,
        trust_account=trust_account,
        cutoff_date=cutoff_date,
        bank_balance_cents=bank_balance,
        cashbook_balance_cents=cashbook_balance,
        subledger_total_cents=subledger_total,
        status=status,
        prepared_by=prepared_by,
    )
