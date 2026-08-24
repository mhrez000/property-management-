"""Rule lookup and domain checks over the compliance rules engine."""

from __future__ import annotations

from datetime import date

from django.db.models import Q

from compliance.models import ComplianceRule, RuleType


class NoRuleFound(Exception):
    pass


def get_rule(jurisdiction: str, rule_type: str, on: date) -> ComplianceRule:
    """The rule version in force in a jurisdiction on a given date."""
    rule = (
        ComplianceRule.objects.filter(
            jurisdiction=jurisdiction,
            rule_type=rule_type,
            effective_from__lte=on,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on))
        .order_by("-effective_from")
        .first()
    )
    if rule is None:
        raise NoRuleFound(f"No {rule_type} rule for {jurisdiction} on {on}")
    return rule


def rent_increase_allowed(
    jurisdiction: str, *, last_increase: date | None, proposed_effective: date, notice_given: date
) -> tuple[bool, list[str]]:
    """Validate a proposed rent increase against notice + frequency rules.

    Returns (allowed, reasons); reasons list every rule the proposal breaks.
    """
    problems: list[str] = []

    notice_rule = get_rule(jurisdiction, RuleType.RENT_INCREASE_NOTICE, proposed_effective)
    min_notice_days = notice_rule.config["min_days"]
    actual_notice = (proposed_effective - notice_given).days
    if actual_notice < min_notice_days:
        problems.append(
            f"{jurisdiction} requires at least {min_notice_days} days notice; only {actual_notice} given."
        )

    freq_rule = get_rule(jurisdiction, RuleType.RENT_INCREASE_FREQUENCY, proposed_effective)
    min_interval_months = freq_rule.config["min_interval_months"]
    if last_increase is not None:
        # Integer month arithmetic — no timedelta approximations.
        elapsed_months = (proposed_effective.year - last_increase.year) * 12 + (
            proposed_effective.month - last_increase.month
        )
        too_soon = elapsed_months < min_interval_months or (
            elapsed_months == min_interval_months and proposed_effective.day < last_increase.day
        )
        if too_soon:
            problems.append(
                f"{jurisdiction} allows increases at most every {min_interval_months} months; "
                f"last increase was {last_increase.isoformat()}."
            )

    return (not problems, problems)


def bond_lodgement_requirements(jurisdiction: str, on: date) -> dict:
    """Where and how fast a bond must be lodged, and the maximum bond."""
    authority = get_rule(jurisdiction, RuleType.BOND_AUTHORITY, on).config
    deadline = get_rule(jurisdiction, RuleType.BOND_LODGEMENT_DEADLINE, on).config
    bond_max = get_rule(jurisdiction, RuleType.BOND_MAX, on).config
    return {"authority": authority, "deadline": deadline, "maximum": bond_max}
