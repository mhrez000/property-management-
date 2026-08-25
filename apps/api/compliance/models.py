"""Effective-dated state compliance rules engine.

Tenancy law differs across all 8 Australian jurisdictions and changes on
specific dates (e.g. VIC rent-increase notice moved from 60 to 90 days on
25 Nov 2025). Rules are therefore data rows — ``(jurisdiction, rule_type,
effective_from, effective_to, config)`` — and the engine picks the version
valid on the relevant date. Reforms become data changes, not code changes.

These tables are global (shared across organisations, no RLS): they describe
the law, not tenant data.
"""

import uuid

from django.db import models

from common.constants import Jurisdiction
from common.models import TimeStampedModel


class RuleType(models.TextChoices):
    BOND_AUTHORITY = "bond_authority", "Bond lodgement authority"
    BOND_MAX = "bond_max", "Maximum bond"
    BOND_LODGEMENT_DEADLINE = "bond_lodgement_deadline", "Bond lodgement deadline"
    RENT_INCREASE_NOTICE = "rent_increase_notice", "Rent increase minimum notice"
    RENT_INCREASE_FREQUENCY = "rent_increase_frequency", "Rent increase frequency limit"
    TRUST_AUDIT = "trust_audit", "Trust account audit period/deadline"
    NO_GROUNDS_TERMINATION = "no_grounds_termination", "No-grounds termination permitted"


class ComplianceRule(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    jurisdiction = models.CharField(max_length=3, choices=Jurisdiction.choices)
    rule_type = models.CharField(max_length=64, choices=RuleType.choices)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    config = models.JSONField(
        help_text="Rule payload, shape depends on rule_type (see compliance.services)."
    )
    source = models.CharField(
        max_length=512, blank=True, help_text="Citation for the rule (act/regulator page)."
    )
    verified = models.BooleanField(
        default=False,
        help_text="False until confirmed against the regulator (research figures are secondary sources).",
    )

    class Meta:
        indexes = [models.Index(fields=["jurisdiction", "rule_type", "effective_from"])]
        constraints = [
            models.UniqueConstraint(
                fields=["jurisdiction", "rule_type", "effective_from"],
                name="uniq_rule_version",
            )
        ]

    def __str__(self):
        return f"{self.jurisdiction} {self.rule_type} from {self.effective_from}"
