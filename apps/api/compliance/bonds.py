"""Bond lodgement workflow services.

Jurisdiction-aware, driven entirely by the compliance rules engine:

- which authority the bond goes to (NT: no central authority — the bond is
  held in the agent's trust account);
- the lodgement deadline (business vs calendar days from receipt);
- the maximum bond (weeks of rent).

Money movements ride the trust ledger: receiving a bond is DR trust bank /
CR bond clearing; lodging with the authority is the reverse (money leaves
trust). In the NT the funds simply stay in trust and "lodgement" is illegal
by construction.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from compliance.models import RuleType
from compliance.services import get_rule
from ledger.services import (
    credit,
    debit,
    ensure_bond_clearing_account,
    ensure_trust_bank_account,
    post_entry,
)
from portfolio.models import Lease, RentSchedule


def add_business_days(start: date, days: int) -> date:
    """Monday–Friday business days; public holidays are jurisdiction data we
    deliberately do not hardcode (conservative: holidays shorten nothing)."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def weekly_rent_equivalent_cents(amount_cents: int, frequency: str) -> int:
    """Normalise a rent amount to a weekly figure for bond-cap maths.

    Integer division truncates — a truncated weekly figure makes the cap
    STRICTER, never more permissive, which is the safe direction.
    """
    if frequency == RentSchedule.Frequency.WEEKLY:
        return amount_cents
    if frequency == RentSchedule.Frequency.FORTNIGHTLY:
        return amount_cents // 2
    if frequency == RentSchedule.Frequency.MONTHLY:
        return amount_cents * 12 // 52
    raise ValueError(f"Unknown rent frequency: {frequency}")


def bond_requirements_for_lease(lease: Lease, on: date | None = None) -> dict:
    """Authority, deadline config and cap for a lease's jurisdiction."""
    on = on or timezone.localdate()
    jurisdiction = lease.tenancy.property.state
    return {
        "jurisdiction": jurisdiction,
        "authority": get_rule(jurisdiction, RuleType.BOND_AUTHORITY, on).config,
        "deadline": get_rule(jurisdiction, RuleType.BOND_LODGEMENT_DEADLINE, on).config,
        "maximum": get_rule(jurisdiction, RuleType.BOND_MAX, on).config,
    }


def validate_bond_amount(lease: Lease, amount_cents: int, on: date | None = None) -> None:
    """Reject bonds over the jurisdiction cap (weeks × weekly rent).

    Some jurisdictions attach conditions (e.g. no cap above a rent
    threshold); the cap check is conservative and a knowing override is a
    data decision for the agency, surfaced via the raised message.
    """
    requirements = bond_requirements_for_lease(lease, on)
    weeks = requirements["maximum"]["weeks"]
    weekly = weekly_rent_equivalent_cents(lease.rent_amount_cents, lease.rent_frequency)
    cap = weeks * weekly
    if amount_cents > cap:
        raise ValidationError(
            f"Bond of {amount_cents} cents exceeds the {requirements['jurisdiction']} cap of "
            f"{weeks} weeks rent ({cap} cents). Conditions: "
            f"{requirements['maximum'].get('conditions') or 'none'}"
        )


def lodgement_due_date(received_on: date, deadline_config: dict) -> date | None:
    """When the bond must reach the authority; None where there is no
    central lodgement (NT)."""
    days = deadline_config["days"]
    if days == 0:
        return None
    if deadline_config["kind"] == "business":
        return add_business_days(received_on, days)
    return received_on + timedelta(days=days)


@transaction.atomic
def create_bond_lodgement(lease: Lease, *, amount_cents: int | None = None):
    """Open the bond workflow for a lease, validated against its state's rules."""
    from compliance.models_bonds import BondLodgement

    amount = amount_cents if amount_cents is not None else lease.bond_amount_cents
    if amount <= 0:
        raise ValidationError("Bond amount must be positive.")
    validate_bond_amount(lease, amount)
    requirements = bond_requirements_for_lease(lease)
    return BondLodgement.objects.create(
        organisation_id=lease.organisation_id,
        lease=lease,
        amount_cents=amount,
        jurisdiction=requirements["jurisdiction"],
        authority_name=requirements["authority"]["name"],
        held_in_agent_trust=requirements["authority"]["held_by_agent_trust"],
    )


@transaction.atomic
def receive_bond(bond, *, trust_account, external_id: str, received_on: date | None = None):
    """Bond money arrives into trust: DR trust bank / CR bond clearing.

    Starts the statutory lodgement clock (except NT, where the money simply
    stays in trust for the life of the tenancy).
    """
    from automation.models import OpsTask
    from compliance.models_bonds import BondLodgement

    bond.transition_to(BondLodgement.State.RECEIVED)
    received_on = received_on or timezone.localdate()

    bank = ensure_trust_bank_account(trust_account)
    clearing = ensure_bond_clearing_account(bond.organisation_id)
    entry = post_entry(
        organisation_id=bond.organisation_id,
        description=f"Bond received — lease {bond.lease_id}",
        lines=[debit(bank, bond.amount_cents), credit(clearing, bond.amount_cents)],
        external_id=external_id,
    )

    bond.trust_account = trust_account
    bond.received_on = received_on
    bond.receipt_entry = entry
    requirements = bond_requirements_for_lease(bond.lease, received_on)
    bond.due_date = lodgement_due_date(received_on, requirements["deadline"])
    if bond.held_in_agent_trust:
        # NT: the trust account IS the bond's home; nothing further to lodge.
        bond.state = BondLodgement.State.HELD_IN_TRUST
    bond.save()

    if bond.due_date is not None:
        OpsTask.objects.create(
            organisation_id=bond.organisation_id,
            title=f"Lodge bond with {bond.authority_name} — {bond.lease.tenancy.reference}",
            description=f"Bond of ${bond.amount_cents / 100:.2f} received {received_on.isoformat()}; "
            f"statutory deadline {bond.due_date.isoformat()} ({bond.jurisdiction}).",
            tenancy=bond.lease.tenancy,
            due_date=bond.due_date,
            assigned_to=bond.lease.tenancy.property.assigned_manager,
        )
    return bond


@transaction.atomic
def mark_bond_lodged(bond, *, authority_reference: str, external_id: str):
    """Money leaves trust for the authority: DR bond clearing / CR trust bank."""
    from compliance.models_bonds import BondLodgement

    if bond.held_in_agent_trust:
        raise ValidationError(
            f"{bond.jurisdiction} has no central bond authority; the bond remains in the agent's "
            "trust account and cannot be lodged."
        )
    bond.transition_to(BondLodgement.State.LODGED)
    bank = ensure_trust_bank_account(bond.trust_account)
    clearing = ensure_bond_clearing_account(bond.organisation_id)
    entry = post_entry(
        organisation_id=bond.organisation_id,
        description=f"Bond lodged with {bond.authority_name} — lease {bond.lease_id}",
        lines=[debit(clearing, bond.amount_cents), credit(bank, bond.amount_cents)],
        external_id=external_id,
    )
    bond.authority_reference = authority_reference
    bond.lodged_at = timezone.now()
    bond.lodgement_entry = entry
    bond.save()
    return bond
