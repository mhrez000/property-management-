"""Allocation + disbursement run tests: split maths, approval gate,
idempotent execution, and end-to-end reconciliation."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError

from common.tenancy import org_context
from ledger.disbursements import (
    allocate_tenancy_funds,
    approve_disbursement_run,
    create_disbursement_run,
    execute_disbursement_run,
    split_by_shares,
)
from ledger.models import DisbursementLine, DisbursementRun, JournalEntry, Reconciliation
from ledger.services import (
    account_balance_cents,
    charge_management_fee,
    ensure_agency_fees_account,
    ensure_owner_account,
    ensure_tenancy_account,
    ensure_trust_bank_account,
    receipt_rent,
    run_three_way_reconciliation,
    trial_balance_cents,
)
from portfolio.models import Owner, OwnerProperty

pytestmark = pytest.mark.django_db


class TestSplitMaths:
    def test_even_split(self):
        assert split_by_shares(10_000, [5000, 5000]) == [5_000, 5_000]

    def test_remainder_goes_to_first_owner(self):
        assert split_by_shares(10_001, [5000, 5000]) == [5_001, 5_000]
        assert split_by_shares(100, [3333, 3333, 3334]) == [34, 33, 33]

    def test_uneven_shares(self):
        assert split_by_shares(100_000, [7000, 3000]) == [70_000, 30_000]

    def test_total_is_always_preserved(self):
        for total in (1, 99, 12_345, 10**9 + 7):
            portions = split_by_shares(total, [1234, 8766])
            assert sum(portions) == total


@pytest.fixture
def funded_tenancy(org, tenancy, trust_account):
    """Tenancy with $2,500 receipted rent; property has two owners (70/30)
    and a 7.7% management fee."""
    with org_context(org.pk):
        prop = tenancy.property
        prop.management_fee_bps = 770
        prop.save(update_fields=["management_fee_bps"])
        alice = Owner.objects.create(organisation=org, name="Alice", email="a@example.com")
        bob = Owner.objects.create(organisation=org, name="Bob", email="b@example.com")
        OwnerProperty.objects.create(
            organisation=org, owner=alice, property=prop, share_basis_points=7000
        )
        OwnerProperty.objects.create(
            organisation=org, owner=bob, property=prop, share_basis_points=3000
        )
        receipt_rent(
            tenancy=tenancy,
            trust_account=trust_account,
            amount_cents=250_000,
            received_from="Tessa",
            external_id="npp:disb-rent-1",
        )
        yield tenancy, alice, bob


class TestAllocation:
    def test_splits_shares_and_deducts_fee(self, org, funded_tenancy):
        tenancy, alice, bob = funded_tenancy
        with org_context(org.pk):
            entries = allocate_tenancy_funds(tenancy)
            assert len(entries) == 2
            # Alice: 70% of 250000 = 175000 gross, fee 7.7% = 13475
            assert account_balance_cents(ensure_owner_account(alice)) == 175_000 - 13_475
            # Bob: 30% = 75000 gross, fee 5775
            assert account_balance_cents(ensure_owner_account(bob)) == 75_000 - 5_775
            assert account_balance_cents(ensure_agency_fees_account(org.pk)) == 13_475 + 5_775
            assert account_balance_cents(ensure_tenancy_account(tenancy)) == 0
            assert trial_balance_cents(org.pk) == 0

    def test_allocation_is_idempotent_via_zero_balance(self, org, funded_tenancy):
        tenancy, _, _ = funded_tenancy
        with org_context(org.pk):
            allocate_tenancy_funds(tenancy)
            assert allocate_tenancy_funds(tenancy) == []
            assert JournalEntry.objects.count() == 3  # 1 receipt + 2 allocations

    def test_property_without_owners_refuses(self, org, tenancy, trust_account):
        with org_context(org.pk):
            receipt_rent(
                tenancy=tenancy,
                trust_account=trust_account,
                amount_cents=10_000,
                received_from="T",
                external_id="npp:disb-rent-2",
            )
            with pytest.raises(ValidationError, match="no owners"):
                allocate_tenancy_funds(tenancy)


class TestDisbursementRuns:
    def _prepared_run(self, org, funded_tenancy, trust_account):
        tenancy, alice, bob = funded_tenancy
        allocate_tenancy_funds(tenancy)
        run = create_disbursement_run(
            trust_account=trust_account, period_end=date(2026, 1, 31)
        )
        return run, alice, bob

    def test_draft_snapshots_positive_balances(self, org, funded_tenancy, trust_account):
        with org_context(org.pk):
            run, alice, bob = self._prepared_run(org, funded_tenancy, trust_account)
            amounts = {l.owner_id: l.amount_cents for l in run.lines.all()}
            assert amounts == {alice.pk: 161_525, bob.pk: 69_225}
            assert run.state == DisbursementRun.State.DRAFT

    def test_execution_requires_approval(self, org, funded_tenancy, trust_account):
        with org_context(org.pk):
            run, _, _ = self._prepared_run(org, funded_tenancy, trust_account)
            with pytest.raises(ValidationError, match="approved"):
                execute_disbursement_run(run)

    def test_approved_run_pays_owners_and_reconciles(self, org, user, funded_tenancy, trust_account):
        from ledger.models import BankTransaction

        with org_context(org.pk):
            run, alice, bob = self._prepared_run(org, funded_tenancy, trust_account)
            approve_disbursement_run(run, approved_by=user)
            execute_disbursement_run(run, executed_by=user)
            run.refresh_from_db()
            assert run.state == DisbursementRun.State.EXECUTED
            assert all(l.status == DisbursementLine.Status.PAID for l in run.lines.all())
            assert account_balance_cents(ensure_owner_account(alice)) == 0
            assert account_balance_cents(ensure_owner_account(bob)) == 0
            # Trust bank retains exactly the undisbursed agency fees.
            assert account_balance_cents(ensure_trust_bank_account(trust_account)) == 19_250

            # Mirror the bank feed and prove the three-way still ties.
            # (Journal entries post "now", so the feed and cut-off use today.)
            from django.utils import timezone

            today = timezone.localdate()
            BankTransaction.objects.create(
                organisation=org,
                trust_account=trust_account,
                date=today,
                amount_cents=250_000,
                external_ref="feed-in",
            )
            BankTransaction.objects.create(
                organisation=org,
                trust_account=trust_account,
                date=today,
                amount_cents=-(161_525 + 69_225),
                external_ref="feed-out",
            )
            recon = run_three_way_reconciliation(
                trust_account=trust_account, cutoff_date=today
            )
            assert recon.status == Reconciliation.Status.BALANCED

    def test_execution_is_idempotent(self, org, user, funded_tenancy, trust_account):
        with org_context(org.pk):
            run, _, _ = self._prepared_run(org, funded_tenancy, trust_account)
            approve_disbursement_run(run, approved_by=user)
            execute_disbursement_run(run)
            entries_after_first = JournalEntry.objects.count()
            with pytest.raises(ValidationError, match="approved"):
                execute_disbursement_run(run)  # state machine blocks a rerun
            assert JournalEntry.objects.count() == entries_after_first

    def test_shrunk_balance_is_skipped_not_partially_paid(
        self, org, user, funded_tenancy, trust_account
    ):
        with org_context(org.pk):
            run, alice, bob = self._prepared_run(org, funded_tenancy, trust_account)
            approve_disbursement_run(run, approved_by=user)
            # A fee lands between draft and execution, shrinking Bob's balance.
            charge_management_fee(owner=bob, amount_cents=1_000)
            execute_disbursement_run(run)
            statuses = {l.owner_id: l.status for l in run.lines.all()}
            assert statuses[alice.pk] == DisbursementLine.Status.PAID
            assert statuses[bob.pk] == DisbursementLine.Status.SKIPPED
            # Bob's money is untouched, ready for the next run.
            assert account_balance_cents(ensure_owner_account(bob)) == 69_225 - 1_000

    def test_cannot_approve_empty_run(self, org, user, trust_account):
        with org_context(org.pk):
            run = create_disbursement_run(
                trust_account=trust_account, period_end=date(2026, 1, 31)
            )
            with pytest.raises(ValidationError, match="empty"):
                approve_disbursement_run(run, approved_by=user)


class TestMoneyRoleGating:
    """Property managers can run the book, but only finance/admin move money."""

    def test_pm_cannot_create_or_execute_runs(self, org, trust_account):
        from accounts.models import Membership, User
        from rest_framework.test import APIClient

        pm = User.objects.create_user("pm2@harbour.example", "test-pass-123")
        Membership.objects.create(
            user=pm, organisation=org, role=Membership.Role.PROPERTY_MANAGER
        )
        api = APIClient()
        api.force_authenticate(pm)
        response = api.post(
            "/api/v1/ledger/disbursement-runs/",
            {"trust_account": str(trust_account.pk), "period_end": "2026-01-31"},
            format="json",
        )
        assert response.status_code == 403

    def test_admin_can_create_run_via_api(self, org, user, funded_tenancy, trust_account):
        from rest_framework.test import APIClient

        tenancy, _, _ = funded_tenancy
        with org_context(org.pk):
            allocate_tenancy_funds(tenancy)
        api = APIClient()
        api.force_authenticate(user)
        response = api.post(
            "/api/v1/ledger/disbursement-runs/",
            {"trust_account": str(trust_account.pk), "period_end": "2026-01-31"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["state"] == "draft"
        assert response.data["total_cents"] == 230_750
        assert len(response.data["lines"]) == 2
