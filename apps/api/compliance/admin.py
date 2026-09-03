from django.contrib import admin

from compliance.models import ComplianceRule
from compliance.models_bonds import BondLodgement


@admin.register(BondLodgement)
class BondLodgementAdmin(admin.ModelAdmin):
    list_display = ("lease", "jurisdiction", "amount_cents", "state", "due_date", "authority_name")
    list_filter = ("state", "jurisdiction")


@admin.register(ComplianceRule)
class ComplianceRuleAdmin(admin.ModelAdmin):
    list_display = ("jurisdiction", "rule_type", "effective_from", "effective_to", "verified")
    list_filter = ("jurisdiction", "rule_type", "verified")
    ordering = ("jurisdiction", "rule_type", "effective_from")
