from django.contrib import admin

from compliance.models import ComplianceRule


@admin.register(ComplianceRule)
class ComplianceRuleAdmin(admin.ModelAdmin):
    list_display = ("jurisdiction", "rule_type", "effective_from", "effective_to", "verified")
    list_filter = ("jurisdiction", "rule_type", "verified")
    ordering = ("jurisdiction", "rule_type", "effective_from")
