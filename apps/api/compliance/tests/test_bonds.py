"""Bond lodgement workflow tests: jurisdiction rules drive everything."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError

from automation.models import OpsTask
from common.tenancy import org_context
from compliance.bonds import (
    add_business_days,
    create_bond_lodgement,
    mark_bond_lodged,
    receive_bond,
    weekly_rent_equivalent_cents,
)
from compliance.models_bonds import BondLodgement
from ledger.services import (
    account_balance_cents,
    ensure_bond_clearing_account,
    ensure_trust_bank_account,
)
from portfolio.models import Lease, Property, Tenancy, Tenant

pytestmark = pytest.mark.django_db


def _lease(org, state="NSW", rent=65_000, frequency="weekly", bond=260_000, ref="TEN-B1"):
    prop = Property.objects.create(
        organisation=org,
        address_line_1="1 Bond St",
        suburb="Testville",
        state=state,
        postcode="2000",
    )
    tenant = Tenant.objects.create(organisation=org, name="Tessa", email="t@example.com")
    tenancy = Tenancy.objects.create(organisation=org, property=prop, reference=ref)
    tenancy.tenants.add(tenant)
    return Lease.objects.create(
        organisation=org,
        tenancy=tenancy,
        state=Lease.State.ACTIVE,
        start_date=date(2026, 1, 1),
        rent_amount_cents=rent,
        rent_frequency=frequency,
        bond_amount_cents=bond,
    )


class TestHelpers:
    def test_business_days_skip_weekends(self):
        # Thu 2026-01-01 + 10 business days = Thu 2026-01-15
        assert add_business_days(date(2026, 1, 1), 10) == date(2026, 1, 15)

    def test_weekly_equivalents_truncate_conservatively(self):
        assert weekly_rent_equivalent_cents(65_000, "weekly") == 65_000
        assert weekly_rent_equivalent_cents(130_000, "fortnightly") == 65_000
        assert weekly_rent_equivalent_cents(282_000, "monthly") == 65_076


class TestBondCreation:
    def test_creates_with_jurisdiction_metadata(self, org):
        with org_context(org.pk):
            bond = create_bond_lodgement(_lease(org, state="NSW"))
            assert bond.jurisdiction == "NSW"
            assert bond.authority_name == "NSW Rental Bonds Online"
            assert bond.held_in_agent_trust is False
            assert bond.state == BondLodgement.State.PENDING_RECEIPT

    def test_rejects_bond_over_cap(self, org):
        with org_context(org.pk):
            # NSW cap: 4 weeks × $650 = $2,600; ask for $2,600.01 worth.
            lease = _lease(org, bond=260_001)
            with pytest.raises(ValidationError, match="exceeds the NSW cap"):
                create_bond_lodgement(lease)

    def test_nt_bond_flagged_as_held_in_trust(self, org):
        with org_context(org.pk):
            bond = create_bond_lodgement(_lease(org, state="NT", ref="TEN-B2"))
            assert bond.held_in_agent_trust is True
            assert bond.authority_name.startswith("No central authority")


class TestBondReceiptAndLodgement:
    def test_receive_posts_to_trust_and_sets_business_day_deadline(self, org, trust_account):
        with org_context(org.pk):
            bond = create_bond_lodgement(_lease(org, state="NSW"))
            receive_bond(
                bond,
                trust_account=trust_account,
                external_id="bond-rcpt-1",
                received_on=date(2026, 1, 1),  # Thursday
            )
            bond.refresh_from_db()
            assert bond.state == BondLodgement.State.RECEIVED
            assert bond.due_date == date(2026, 1, 15)  # 10 business days
            assert account_balance_cents(ensure_trust_bank_account(trust_account)) == 260_000
            assert account_balance_cents(ensure_bond_clearing_account(org.pk)) == 260_000
            task = OpsTask.objects.get()
            assert "Lodge bond" in task.title
            assert task.due_date == bond.due_date

    def test_lodgement_moves_money_out_of_trust(self, org, trust_account):
        with org_context(org.pk):
            bond = create_bond_lodgement(_lease(org, state="NSW"))
            receive_bond(bond, trust_account=trust_account, external_id="bond-rcpt-2")
            mark_bond_lodged(bond, authority_reference="RBO-12345", external_id="bond-lodge-2")
            bond.refresh_from_db()
            assert bond.state == BondLodgement.State.LODGED
            assert bond.authority_reference == "RBO-12345"
            assert account_balance_cents(ensure_trust_bank_account(trust_account)) == 0
            assert account_balance_cents(ensure_bond_clearing_account(org.pk)) == 0

    def test_nt_bond_stays_in_trust_and_cannot_be_lodged(self, org, trust_account):
        with org_context(org.pk):
            bond = create_bond_lodgement(_lease(org, state="NT", ref="TEN-B3"))
            receive_bond(bond, trust_account=trust_account, external_id="bond-rcpt-3")
            bond.refresh_from_db()
            assert bond.state == BondLodgement.State.HELD_IN_TRUST
            assert bond.due_date is None
            assert OpsTask.objects.count() == 0  # no lodgement deadline to chase
            with pytest.raises(ValidationError, match="no central bond authority"):
                mark_bond_lodged(bond, authority_reference="X", external_id="bond-lodge-3")
            # The money remains in trust.
            assert account_balance_cents(ensure_trust_bank_account(trust_account)) == 260_000

    def test_receive_is_guarded_by_state_machine(self, org, trust_account):
        with org_context(org.pk):
            bond = create_bond_lodgement(_lease(org, state="NSW"))
            receive_bond(bond, trust_account=trust_account, external_id="bond-rcpt-4")
            bond.refresh_from_db()
            with pytest.raises(ValidationError, match="Invalid bond transition"):
                receive_bond(bond, trust_account=trust_account, external_id="bond-rcpt-4b")

    def test_wa_calendar_day_deadline(self, org, trust_account):
        with org_context(org.pk):
            bond = create_bond_lodgement(_lease(org, state="WA", ref="TEN-B4"))
            receive_bond(
                bond,
                trust_account=trust_account,
                external_id="bond-rcpt-5",
                received_on=date(2026, 1, 1),
            )
            bond.refresh_from_db()
            assert bond.due_date == date(2026, 1, 15)  # 14 calendar days
