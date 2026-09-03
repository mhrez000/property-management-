"""Trust-accounting double-entry ledger.

Design invariants (enforced at the database level, not just in Python):

- Money is integer cents (BIGINT). Never floats, never NUMERIC arithmetic in
  the application.
- ``JournalEntry`` + ``Posting`` rows are immutable: Postgres triggers raise
  on UPDATE/DELETE. Corrections are reversing entries (``reverses`` FK on the
  new entry — the original row is never touched).
- Every entry balances: a deferred constraint trigger verifies
  Σdebits = Σcredits and ≥2 postings per entry at commit.
- Statutory receipts are gap-free per trust account: numbers come from a
  row-locked counter inside the same transaction as the receipt, so a
  rollback returns the number (a plain Postgres SEQUENCE would leave gaps).
- Balances are always derived from postings — never stored as mutable truth.

The account structure per organisation:

- one **asset** account per trust bank account;
- one **liability** sub-account per owner and per tenancy (the statutory
  client sub-ledgers);
- a **liability** account for agency fees cleared out of the trust.

Three-way reconciliation: bank statement balance = trust cashbook (the asset
account) = Σ(client sub-ledger balances), to the cent, at a cut-off date.
"""

from django.db import models

from common.models import OrgScopedModel


class TrustAccount(OrgScopedModel):
    """A statutory trust bank account held by the agency."""

    name = models.CharField(max_length=255)
    bsb = models.CharField(max_length=7, blank=True)
    account_number = models.CharField(max_length=32, blank=True)

    def __str__(self):
        return self.name


class Account(OrgScopedModel):
    """A ledger account (chart of accounts)."""

    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    class Subtype(models.TextChoices):
        TRUST_BANK = "trust_bank", "Trust bank account"
        TENANCY_LEDGER = "tenancy_ledger", "Tenancy sub-ledger"
        OWNER_LEDGER = "owner_ledger", "Owner sub-ledger"
        AGENCY_FEES = "agency_fees", "Agency fees clearing"
        BOND_CLEARING = "bond_clearing", "Bond clearing"
        OTHER = "other", "Other"

    name = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=Type.choices)
    subtype = models.CharField(max_length=32, choices=Subtype.choices, default=Subtype.OTHER)
    trust_account = models.ForeignKey(
        TrustAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="accounts"
    )
    # Exactly one sub-ledger account per owner / per tenancy.
    owner = models.OneToOneField(
        "portfolio.Owner",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_account",
    )
    tenancy = models.OneToOneField(
        "portfolio.Tenancy",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_account",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["organisation", "subtype"])]

    DEBIT_NORMAL_TYPES = {Type.ASSET, Type.EXPENSE}

    @property
    def is_debit_normal(self) -> bool:
        return self.Type(self.type) in self.DEBIT_NORMAL_TYPES

    def __str__(self):
        return self.name


class JournalEntry(OrgScopedModel):
    """Immutable transaction header. Mutation is blocked by a DB trigger."""

    description = models.CharField(max_length=512)
    posted_at = models.DateTimeField()
    # Idempotency key (e.g. payment provider transaction id): a retried
    # webhook creates one entry, not two.
    external_id = models.CharField(max_length=255, null=True, blank=True)
    # Set on the REVERSING entry, pointing at the entry it reverses; the
    # original row is never modified.
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by"
    )
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name_plural = "journal entries"
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "external_id"],
                name="uniq_entry_external_id",
                condition=models.Q(external_id__isnull=False),
            )
        ]
        indexes = [models.Index(fields=["organisation", "posted_at"])]

    def __str__(self):
        return f"{self.posted_at:%Y-%m-%d} {self.description}"


class Posting(OrgScopedModel):
    """Immutable double-entry line. ``side`` is explicit — no signed amounts."""

    class Side(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    journal_entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="postings"
    )
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="postings")
    side = models.CharField(max_length=6, choices=Side.choices)
    amount_cents = models.BigIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(amount_cents__gt=0), name="posting_amount_positive")
        ]
        indexes = [models.Index(fields=["organisation", "account"])]


class ReceiptSequence(OrgScopedModel):
    """Row-locked gap-free counter, one per trust account."""

    trust_account = models.OneToOneField(
        TrustAccount, on_delete=models.PROTECT, related_name="receipt_sequence"
    )
    next_number = models.BigIntegerField(default=1)


class Receipt(OrgScopedModel):
    """A statutory trust receipt tied to a journal entry.

    Immutable (DB trigger); numbering is sequential without gaps per trust
    account, as state trust-account regulations require.
    """

    trust_account = models.ForeignKey(TrustAccount, on_delete=models.PROTECT, related_name="receipts")
    number = models.BigIntegerField()
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, related_name="receipt"
    )
    amount_cents = models.BigIntegerField()
    received_from = models.CharField(max_length=255)
    method = models.CharField(max_length=32, default="eft")
    issued_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["trust_account", "number"], name="uniq_receipt_number_per_trust_account"
            ),
            models.CheckConstraint(check=models.Q(amount_cents__gt=0), name="receipt_amount_positive"),
        ]
        indexes = [models.Index(fields=["organisation", "trust_account", "number"])]


class BankTransaction(OrgScopedModel):
    """A line from the trust bank statement/feed, for reconciliation."""

    trust_account = models.ForeignKey(
        TrustAccount, on_delete=models.PROTECT, related_name="bank_transactions"
    )
    date = models.DateField()
    amount_cents = models.BigIntegerField(help_text="Signed: positive = money in, negative = out.")
    description = models.CharField(max_length=512, blank=True)
    external_ref = models.CharField(max_length=255)
    matched_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="bank_matches"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["trust_account", "external_ref"], name="uniq_bank_txn_external_ref"
            )
        ]
        indexes = [models.Index(fields=["organisation", "trust_account", "date"])]


class DisbursementRun(OrgScopedModel):
    """A month-end owner payout batch with an explicit approval gate.

    Money movement is never automatic: a run is drafted (snapshotting owner
    balances), APPROVED by a human with a finance role, and only then
    executed. Execution is idempotent — each line's payout entry is keyed on
    the line id, so a crashed run can be re-executed without double-paying.
    """

    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        EXECUTED = "executed", "Executed"
        CANCELLED = "cancelled", "Cancelled"

    ALLOWED_TRANSITIONS = {
        State.DRAFT: {State.APPROVED, State.CANCELLED},
        State.APPROVED: {State.EXECUTED, State.CANCELLED},
        State.EXECUTED: set(),
        State.CANCELLED: set(),
    }

    trust_account = models.ForeignKey(
        TrustAccount, on_delete=models.PROTECT, related_name="disbursement_runs"
    )
    period_end = models.DateField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organisation", "state"])]

    def transition_to(self, new_state: str):
        from django.core.exceptions import ValidationError

        allowed = self.ALLOWED_TRANSITIONS[self.State(self.state)]
        if self.State(new_state) not in allowed:
            raise ValidationError(
                f"Invalid disbursement run transition {self.state} → {new_state}"
            )
        self.state = new_state


class DisbursementLine(OrgScopedModel):
    """One owner's payout within a run, snapshotted at draft time."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        SKIPPED = "skipped", "Skipped (insufficient balance at execution)"

    run = models.ForeignKey(DisbursementRun, on_delete=models.CASCADE, related_name="lines")
    owner = models.ForeignKey("portfolio.Owner", on_delete=models.PROTECT, related_name="+")
    amount_cents = models.BigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "owner"], name="uniq_disbursement_line_owner"),
            models.CheckConstraint(
                check=models.Q(amount_cents__gt=0), name="disbursement_amount_positive"
            ),
        ]


class Reconciliation(OrgScopedModel):
    """A monthly three-way reconciliation record (the auditor's artefact)."""

    class Status(models.TextChoices):
        BALANCED = "balanced", "Balanced"
        OUT_OF_BALANCE = "out_of_balance", "Out of balance"

    trust_account = models.ForeignKey(
        TrustAccount, on_delete=models.PROTECT, related_name="reconciliations"
    )
    cutoff_date = models.DateField()
    bank_balance_cents = models.BigIntegerField()
    cashbook_balance_cents = models.BigIntegerField()
    subledger_total_cents = models.BigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices)
    prepared_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["trust_account", "cutoff_date"], name="uniq_reconciliation_cutoff"
            )
        ]
