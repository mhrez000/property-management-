from datetime import date

import pytest

from compliance.models import RuleType
from compliance.services import (
    NoRuleFound,
    bond_lodgement_requirements,
    get_rule,
    rent_increase_allowed,
)

pytestmark = pytest.mark.django_db


class TestEffectiveDatedSelection:
    def test_vic_notice_period_changes_on_reform_date(self):
        """VIC rent-increase notice: 60 days before 25 Nov 2025, 90 after."""
        before = get_rule("VIC", RuleType.RENT_INCREASE_NOTICE, date(2025, 11, 24))
        after = get_rule("VIC", RuleType.RENT_INCREASE_NOTICE, date(2025, 11, 25))
        assert before.config["min_days"] == 60
        assert after.config["min_days"] == 90

    def test_nsw_no_grounds_ban_is_effective_dated(self):
        before = get_rule("NSW", RuleType.NO_GROUNDS_TERMINATION, date(2025, 5, 18))
        after = get_rule("NSW", RuleType.NO_GROUNDS_TERMINATION, date(2025, 5, 19))
        assert before.config["permitted"] is True
        assert after.config["permitted"] is False

    def test_all_eight_jurisdictions_seeded(self):
        for jurisdiction in ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]:
            for rule_type in [
                RuleType.BOND_AUTHORITY,
                RuleType.BOND_MAX,
                RuleType.BOND_LODGEMENT_DEADLINE,
                RuleType.RENT_INCREASE_NOTICE,
                RuleType.RENT_INCREASE_FREQUENCY,
                RuleType.TRUST_AUDIT,
                RuleType.NO_GROUNDS_TERMINATION,
            ]:
                get_rule(jurisdiction, rule_type, date(2026, 8, 1))  # raises if missing

    def test_missing_rule_raises(self):
        with pytest.raises(NoRuleFound):
            get_rule("NSW", RuleType.RENT_INCREASE_NOTICE, date(1999, 1, 1))


class TestRentIncreaseValidation:
    def test_valid_increase_passes(self):
        allowed, problems = rent_increase_allowed(
            "NSW",
            last_increase=date(2025, 1, 1),
            proposed_effective=date(2026, 3, 1),
            notice_given=date(2025, 12, 1),
        )
        assert allowed
        assert problems == []

    def test_short_notice_fails(self):
        allowed, problems = rent_increase_allowed(
            "VIC",
            last_increase=None,
            proposed_effective=date(2026, 2, 1),
            notice_given=date(2026, 1, 1),  # 31 days, VIC needs 90
        )
        assert not allowed
        assert "90 days" in problems[0]

    def test_too_frequent_fails(self):
        allowed, problems = rent_increase_allowed(
            "QLD",
            last_increase=date(2026, 1, 1),
            proposed_effective=date(2026, 9, 1),  # 8 months later
            notice_given=date(2026, 6, 1),
        )
        assert not allowed
        assert any("every 12 months" in p for p in problems)

    def test_nt_allows_six_monthly(self):
        allowed, problems = rent_increase_allowed(
            "NT",
            last_increase=date(2026, 1, 1),
            proposed_effective=date(2026, 8, 1),  # 7 months later
            notice_given=date(2026, 6, 15),  # 47 days ≥ 30
        )
        assert allowed


class TestBondRequirements:
    def test_nt_bonds_stay_in_agent_trust(self):
        requirements = bond_lodgement_requirements("NT", date(2026, 8, 1))
        assert requirements["authority"]["held_by_agent_trust"] is True

    def test_nsw_bond_window(self):
        requirements = bond_lodgement_requirements("NSW", date(2026, 8, 1))
        assert requirements["deadline"]["days"] == 10
        assert requirements["deadline"]["kind"] == "business"
        assert requirements["maximum"]["weeks"] == 4
