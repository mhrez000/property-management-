"""Seed rules for all 8 jurisdictions, sourced from the founding research.

⚠️ Figures marked ``verified=False`` rest on secondary sources — confirm each
against the relevant regulator (Fair Trading NSW, CAV, RTA QLD, Consumer
Protection WA, CBS SA, CBOS TAS, Access Canberra, NT Consumer Affairs) before
production use. That is a data-correction exercise, by design.

Config shapes by rule type:

- bond_authority: {"name": str, "held_by_agent_trust": bool}
- bond_max: {"weeks": int, "conditions": str}
- bond_lodgement_deadline: {"days": int, "kind": "business"|"calendar", "notes": str}
- rent_increase_notice: {"min_days": int}
- rent_increase_frequency: {"min_interval_months": int}
- trust_audit: {"period": str, "due": str}
- no_grounds_termination: {"permitted": bool, "notes": str}
"""

from datetime import date

EPOCH = date(2020, 1, 1)  # "since before the platform existed"

SEED_RULES = [
    # ----- Bond authorities -----
    ("NSW", "bond_authority", EPOCH, None, {"name": "NSW Rental Bonds Online", "held_by_agent_trust": False}),
    ("VIC", "bond_authority", EPOCH, None, {"name": "Residential Tenancies Bond Authority (RTBA)", "held_by_agent_trust": False}),
    ("QLD", "bond_authority", EPOCH, None, {"name": "Residential Tenancies Authority (RTA)", "held_by_agent_trust": False}),
    ("WA", "bond_authority", EPOCH, None, {"name": "Bond Administrator (Consumer Protection WA)", "held_by_agent_trust": False}),
    ("SA", "bond_authority", EPOCH, None, {"name": "Consumer and Business Services (CBS) Residential Bonds Online", "held_by_agent_trust": False}),
    ("TAS", "bond_authority", EPOCH, None, {"name": "Rental Deposit Authority (MyBond)", "held_by_agent_trust": False}),
    ("ACT", "bond_authority", EPOCH, None, {"name": "ACT Rental Bonds Office (Access Canberra)", "held_by_agent_trust": False}),
    ("NT", "bond_authority", EPOCH, None, {"name": "No central authority — bond held in agent trust account", "held_by_agent_trust": True}),
    # ----- Bond maximums -----
    ("NSW", "bond_max", EPOCH, None, {"weeks": 4, "conditions": ""}),
    ("VIC", "bond_max", EPOCH, None, {"weeks": 4, "conditions": "1 month where rent ≤ $900/wk; no statutory cap above"}),
    ("QLD", "bond_max", date(2024, 9, 30), None, {"weeks": 4, "conditions": "All tenancies from 30 Sep 2024; pet bonds prohibited"}),
    ("WA", "bond_max", EPOCH, None, {"weeks": 4, "conditions": "Where rent ≤ $1,200/wk; separate pet bond permitted"}),
    ("SA", "bond_max", EPOCH, None, {"weeks": 4, "conditions": "4 weeks where rent ≤ $800/wk, else 6 weeks"}),
    ("TAS", "bond_max", EPOCH, None, {"weeks": 4, "conditions": ""}),
    ("ACT", "bond_max", EPOCH, None, {"weeks": 4, "conditions": ""}),
    ("NT", "bond_max", EPOCH, None, {"weeks": 4, "conditions": "Pet bonds not permitted"}),
    # ----- Bond lodgement deadlines -----
    ("NSW", "bond_lodgement_deadline", EPOCH, None, {"days": 10, "kind": "business", "notes": "Rental Bonds Online"}),
    ("VIC", "bond_lodgement_deadline", EPOCH, None, {"days": 10, "kind": "business", "notes": "To RTBA"}),
    ("QLD", "bond_lodgement_deadline", EPOCH, None, {"days": 10, "kind": "calendar", "notes": "To RTA"}),
    ("WA", "bond_lodgement_deadline", EPOCH, None, {"days": 14, "kind": "calendar", "notes": "To Bond Administrator"}),
    ("SA", "bond_lodgement_deadline", EPOCH, None, {"days": 28, "kind": "calendar", "notes": "4 weeks via agent (2 weeks if self-managing landlord)"}),
    ("TAS", "bond_lodgement_deadline", EPOCH, None, {"days": 3, "kind": "business", "notes": "Agents, via MyBond; tenant may pay RDA directly"}),
    ("ACT", "bond_lodgement_deadline", EPOCH, None, {"days": 28, "kind": "calendar", "notes": "4 weeks via agent (2 weeks if lessor self-managing)"}),
    ("NT", "bond_lodgement_deadline", EPOCH, None, {"days": 0, "kind": "calendar", "notes": "No central lodgement — held in agent trust account"}),
    # ----- Rent increase minimum notice -----
    ("NSW", "rent_increase_notice", EPOCH, None, {"min_days": 60}),
    ("VIC", "rent_increase_notice", EPOCH, date(2025, 11, 24), {"min_days": 60}),
    ("VIC", "rent_increase_notice", date(2025, 11, 25), None, {"min_days": 90}),
    ("QLD", "rent_increase_notice", EPOCH, None, {"min_days": 60}),
    ("WA", "rent_increase_notice", EPOCH, None, {"min_days": 60}),
    ("SA", "rent_increase_notice", EPOCH, None, {"min_days": 60}),
    ("TAS", "rent_increase_notice", EPOCH, None, {"min_days": 60}),
    ("ACT", "rent_increase_notice", EPOCH, None, {"min_days": 56}),
    ("NT", "rent_increase_notice", EPOCH, None, {"min_days": 30}),
    # ----- Rent increase frequency -----
    ("NSW", "rent_increase_frequency", date(2024, 10, 31), None, {"min_interval_months": 12}),
    ("VIC", "rent_increase_frequency", EPOCH, None, {"min_interval_months": 12}),
    ("QLD", "rent_increase_frequency", date(2024, 6, 6), None, {"min_interval_months": 12, "attaches_to": "premises"}),
    ("WA", "rent_increase_frequency", EPOCH, None, {"min_interval_months": 12, "attaches_to": "premises"}),
    ("SA", "rent_increase_frequency", EPOCH, None, {"min_interval_months": 12}),
    ("TAS", "rent_increase_frequency", EPOCH, None, {"min_interval_months": 12}),
    ("ACT", "rent_increase_frequency", EPOCH, None, {"min_interval_months": 12, "cpi_linked_cap": True}),
    ("NT", "rent_increase_frequency", EPOCH, None, {"min_interval_months": 6}),
    # ----- Trust audit periods -----
    ("NSW", "trust_audit", EPOCH, None, {"period": "Year to 30 June", "due": "30 September"}),
    ("VIC", "trust_audit", EPOCH, None, {"period": "1 July – 30 June", "due": "Audit within 3 months; lodge via myCAV within 10 business days"}),
    ("QLD", "trust_audit", EPOCH, None, {"period": "Licence-anniversary year", "due": "Lodge within 4 months"}),
    ("WA", "trust_audit", EPOCH, None, {"period": "Calendar year to 31 December", "due": "31 March"}),
    ("SA", "trust_audit", EPOCH, None, {"period": "Annual (Land Agents Act)", "due": "Verify with CBS"}),
    ("TAS", "trust_audit", EPOCH, None, {"period": "Annual (CBOS)", "due": "Verify with CBOS"}),
    ("ACT", "trust_audit", EPOCH, None, {"period": "Annual (Access Canberra)", "due": "Verify with Access Canberra"}),
    ("NT", "trust_audit", EPOCH, None, {"period": "Annual (NT Consumer Affairs)", "due": "Verify with NT Consumer Affairs"}),
    # ----- No-grounds termination (effective-dated bans) -----
    ("NSW", "no_grounds_termination", EPOCH, date(2025, 5, 18), {"permitted": True, "notes": ""}),
    ("NSW", "no_grounds_termination", date(2025, 5, 19), None, {"permitted": False, "notes": "Banned 19 May 2025"}),
    ("VIC", "no_grounds_termination", EPOCH, date(2025, 11, 24), {"permitted": True, "notes": ""}),
    ("VIC", "no_grounds_termination", date(2025, 11, 25), None, {"permitted": False, "notes": "Abolished 25 Nov 2025"}),
    ("SA", "no_grounds_termination", EPOCH, date(2025, 12, 31), {"permitted": True, "notes": ""}),
    ("SA", "no_grounds_termination", date(2026, 1, 1), None, {"permitted": False, "notes": "Restricted from 1 Jan 2026"}),
    ("QLD", "no_grounds_termination", EPOCH, None, {"permitted": True, "notes": ""}),
    ("WA", "no_grounds_termination", EPOCH, None, {"permitted": True, "notes": "Not abolished in 2024 reforms"}),
    ("TAS", "no_grounds_termination", EPOCH, None, {"permitted": True, "notes": ""}),
    ("ACT", "no_grounds_termination", EPOCH, None, {"permitted": False, "notes": "Already abolished"}),
    ("NT", "no_grounds_termination", EPOCH, None, {"permitted": True, "notes": "60-day notice for tenancies from 2 Jan 2024"}),
]


def load_seed_rules(ComplianceRule):
    """Idempotently insert the seed rules (used by the data migration)."""
    for jurisdiction, rule_type, effective_from, effective_to, config in SEED_RULES:
        ComplianceRule.objects.update_or_create(
            jurisdiction=jurisdiction,
            rule_type=rule_type,
            effective_from=effective_from,
            defaults={
                "effective_to": effective_to,
                "config": config,
                "source": "Founding research documents (secondary sources)",
                "verified": False,
            },
        )
