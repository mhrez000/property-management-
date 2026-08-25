"""Ledger engine tests: the compliance heart of the product.

Covers the service-layer validation AND the database backstops (balancing
constraint trigger, immutability triggers, gap-free receipts), plus the trust
workflows through to a balanced three-way reconciliation.
"""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.utils import timezone

from common.tenancy import org_context
from ledger.models import Account, JournalEntry, Posting, Receipt, Reconciliation
from ledger.services import (
    account_balance_cents,
    allocate_rent_to_owner,
    charge_management_fee,
    credit,
    debit,
    disburse_to_owner,
    ensure_owner_account,
    ensure_tenancy_account,
    ensure_trust_bank_account,
    post_entry,
    receipt_rent,
    reverse_entry,
    run_three_way_reconciliation,
    trial_balance_cents,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def ctx(org):
    with org_context(org.pk):
        yield org


@pytest.fixture
def two_accounts(ctx, trust_account):
    bank = ensure_trust_bank_account(trust_account)
    other = Account.objects.create(
        organisation=ctx,
        name="Suspense",
        type=Account.Type.LIABILITY,
        subtype=Account.Subtype.OTHER,
    )
    return bank, other


class TestPostEntry:
    def test_posts_balanced_entry(self, ctx, two_accounts):
        bank, other = two_accounts
        entry = post_entry(
            organisation_id=ctx.pk,
            description="Test receipt",
            lines=[debit(bank, 50_000), credit(other, 50_000)],
        )
        assert entry.postings.count() == 2
        assert account_balance_cents(bank) == 50_000
        assert account_balance_cents(other) == 50_000
        assert trial_balance_cents(ctx.pk) == 0

    def test_rejects_unbalanced_entry(self, ctx, two_accounts):
        bank, other = two_accounts
        with pytest.raises(ValidationError, match="does not balance"):
            post_entry(
                organisation_id=ctx.pk,
                description="Bad",
                lines=[debit(bank, 100), credit(other, 99)],
            )

    def test_rejects_single_line(self, ctx, two_accounts):
        bank, _ = two_accounts
        with pytest.raises(ValidationError, match="at least two"):
            post_entry(organisation_id=ctx.pk, description="Bad", lines=[debit(bank, 100)])

    def test_rejects_non_integer_and_non_positive_amounts(self, ctx, two_accounts):
        bank, other = two_accounts
        for bad in (0, -5, 10.5, True):
            with pytest.raises(ValidationError):
                post_entry(
                    organisation_id=ctx.pk,
                    description="Bad",
                    lines=[
                        debit(bank, bad),
                        credit(other, bad if not isinstance(bad, float) else int(bad)),
                    ],
                )

    def test_idempotent_on_external_id(self, ctx, two_accounts):
        bank, other = two_accounts
        lines = [debit(bank, 1_000), credit(other, 1_000)]
        first = post_entry(
            organisation_id=ctx.pk, description="Webhook", lines=lines, external_id="txn-1"
        )
        second = post_entry(
            organisation_id=ctx.pk, description="Webhook retry", lines=lines, external_id="txn-1"
        )
        assert first.pk == second.pk
        assert JournalEntry.objects.count() == 1


class TestDatabaseBackstops:
    def test_db_rejects_unbalanced_entry_bypassing_service(self, ctx, two_accounts):
        """Raw inserts that skip post_entry must be caught by the trigger."""
        bank, other = two_accounts
        with pytest.raises(DatabaseError, match="unbalanced"):
            with transaction.atomic():
                entry = JournalEntry.objects.create(
                    organisation=ctx, description="Sneaky", posted_at=timezone.now()
                )
                Posting.objects.create(
                    organisation=ctx,
                    journal_entry=entry,
                    account=bank,
                    side="debit",
                    amount_cents=100,
                )
                Posting.objects.create(
                    organisation=ctx,
                    journal_entry=entry,
                    account=other,
                    side="credit",
                    amount_cents=42,
                )
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def test_db_rejects_lonely_posting(self, ctx, two_accounts):
        bank, _ = two_accounts
        with pytest.raises(DatabaseError, match="at least two"):
            with transaction.atomic():
                entry = JournalEntry.objects.create(
                    organisation=ctx, description="Sneaky", posted_at=timezone.now()
                )
                Posting.objects.create(
                    organisation=ctx,
                    journal_entry=entry,
                    account=bank,
                    side="debit",
                    amount_cents=100,
                )
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def test_postings_are_immutable(self, ctx, two_accounts):
        bank, other = two_accounts
        entry = post_entry(
            organisation_id=ctx.pk,
            description="Locked",
            lines=[debit(bank, 100), credit(other, 100)],
        )
        posting = entry.postings.first()
        with pytest.raises(DatabaseError, match="immutable"):
            with transaction.atomic():
                Posting.objects.filter(pk=posting.pk).update(amount_cents=999)
        with pytest.raises(DatabaseError, match="immutable"):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM ledger_posting WHERE id = %s", [str(posting.pk)]
                    )

    def test_journal_entries_are_immutable(self, ctx, two_accounts):
        bank, other = two_accounts
        entry = post_entry(
            organisation_id=ctx.pk,
            description="Locked",
            lines=[debit(bank, 100), credit(other, 100)],
        )
        with pytest.raises(DatabaseError, match="immutable"):
            with transaction.atomic():
                JournalEntry.objects.filter(pk=entry.pk).update(description="Edited")


class TestReversals:
    def test_reversal_mirrors_and_nets_to_zero(self, ctx, two_accounts):
        bank, other = two_accounts
        entry = post_entry(
            organisation_id=ctx.pk,
            description="Oops",
            lines=[debit(bank, 7_700), credit(other, 7_700)],
        )
        reversal = reverse_entry(entry)
        assert reversal.reverses_id == entry.pk
        assert account_balance_cents(bank) == 0
        assert account_balance_cents(other) == 0
        assert trial_balance_cents(ctx.pk) == 0

    def test_cannot_reverse_twice(self, ctx, two_accounts):
        bank, other = two_accounts
        entry = post_entry(
            organisation_id=ctx.pk,
            description="Oops",
            lines=[debit(bank, 100), credit(other, 100)],
        )
        reverse_entry(entry)
        entry = JournalEntry.objects.get(pk=entry.pk)
        with pytest.raises(ValidationError, match="already been reversed"):
            reverse_entry(entry)


class TestReceipts:
    def test_receipt_numbers_are_sequential_without_gaps(self, ctx, trust_account, tenancy):
        numbers = []
        for i in range(5):
            receipt = receipt_rent(
                tenancy=tenancy,
                trust_account=trust_account,
                amount_cents=10_000,
                received_from="Tessa Tenant",
                external_id=f"txn-{i}",
            )
            numbers.append(receipt.number)
        assert numbers == [1, 2, 3, 4, 5]

    def test_rolled_back_receipt_number_is_reused(self, ctx, trust_account, tenancy):
        receipt_rent(
            tenancy=tenancy,
            trust_account=trust_account,
            amount_cents=10_000,
            received_from="T",
            external_id="txn-a",
        )
        try:
            with transaction.atomic():
                receipt_rent(
                    tenancy=tenancy,
                    trust_account=trust_account,
                    amount_cents=10_000,
                    received_from="T",
                    external_id="txn-b",
                )
                raise RuntimeError("abort")
        except RuntimeError:
            pass
        receipt = receipt_rent(
            tenancy=tenancy,
            trust_account=trust_account,
            amount_cents=10_000,
            received_from="T",
            external_id="txn-c",
        )
        # The aborted allocation (2) must be reissued, not skipped.
        assert receipt.number == 2

    def test_receipt_rent_is_idempotent(self, ctx, trust_account, tenancy):
        first = receipt_rent(
            tenancy=tenancy,
            trust_account=trust_account,
            amount_cents=10_000,
            received_from="T",
            external_id="npp:abc",
        )
        retry = receipt_rent(
            tenancy=tenancy,
            trust_account=trust_account,
            amount_cents=10_000,
            received_from="T",
            external_id="npp:abc",
        )
        assert first.pk == retry.pk
        assert Receipt.objects.count() == 1


class TestTrustWorkflows:
    def test_full_cycle_reconciles_three_ways(self, ctx, trust_account, tenancy):
        """Rent in → allocate to owner → fee → disbursement, then
        bank = cashbook = Σ sub-ledgers to the cent."""
        from ledger.models import BankTransaction
        from portfolio.models import Owner

        owner = Owner.objects.create(organisation=ctx, name="Olive Owner")

        receipt = receipt_rent(
            tenancy=tenancy,
            trust_account=trust_account,
            amount_cents=250_000,
            received_from="Tessa Tenant",
            external_id="npp:rent-1",
        )
        BankTransaction.objects.create(
            organisation=ctx,
            trust_account=trust_account,
            date=date.today(),
            amount_cents=250_000,
            description="NPP credit",
            external_ref="bank-1",
            matched_entry=receipt.journal_entry,
        )
        allocate_rent_to_owner(tenancy=tenancy, owner=owner, amount_cents=250_000)
        charge_management_fee(owner=owner, amount_cents=19_250)  # 7.7% incl GST
        disburse_to_owner(
            owner=owner,
            trust_account=trust_account,
            amount_cents=230_750,
            external_id="disb-1",
        )
        BankTransaction.objects.create(
            organisation=ctx,
            trust_account=trust_account,
            date=date.today(),
            amount_cents=-230_750,
            description="Owner EFT",
            external_ref="bank-2",
        )

        recon = run_three_way_reconciliation(
            trust_account=trust_account, cutoff_date=date.today()
        )
        assert recon.status == Reconciliation.Status.BALANCED
        assert recon.bank_balance_cents == 19_250  # the undisbursed agency fee
        assert recon.cashbook_balance_cents == 19_250
        assert recon.subledger_total_cents == 19_250
        assert account_balance_cents(ensure_tenancy_account(tenancy)) == 0
        assert account_balance_cents(ensure_owner_account(owner)) == 0

    def test_disbursement_cannot_overdraw_owner_ledger(self, ctx, trust_account, tenancy):
        from portfolio.models import Owner

        owner = Owner.objects.create(organisation=ctx, name="Olive Owner")
        receipt_rent(
            tenancy=tenancy,
            trust_account=trust_account,
            amount_cents=50_000,
            received_from="T",
            external_id="npp:rent-2",
        )
        allocate_rent_to_owner(tenancy=tenancy, owner=owner, amount_cents=50_000)
        with pytest.raises(ValidationError, match="deficit"):
            disburse_to_owner(owner=owner, trust_account=trust_account, amount_cents=50_001)

    def test_out_of_balance_reconciliation_is_flagged(self, ctx, trust_account, tenancy):
        receipt_rent(
            tenancy=tenancy,
            trust_account=trust_account,
            amount_cents=100_000,
            received_from="T",
            external_id="npp:rent-3",
        )
        # No bank transaction recorded → bank side is short.
        recon = run_three_way_reconciliation(
            trust_account=trust_account, cutoff_date=date.today()
        )
        assert recon.status == Reconciliation.Status.OUT_OF_BALANCE
