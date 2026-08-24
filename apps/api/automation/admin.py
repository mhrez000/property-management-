from django.contrib import admin

from automation.models import (
    ArrearsCase,
    AutomationRule,
    Communication,
    CommunicationTemplate,
    OpsTask,
    OutboxEvent,
)


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "trigger", "is_active")
    list_filter = ("trigger", "is_active")


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "occurred_at", "processed_at", "attempts")
    list_filter = ("event_type",)
    readonly_fields = ("event_type", "payload", "occurred_at")


@admin.register(OpsTask)
class OpsTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "tenancy", "assigned_to", "status", "due_date")
    list_filter = ("status",)


@admin.register(CommunicationTemplate)
class CommunicationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "channel")


@admin.register(Communication)
class CommunicationAdmin(admin.ModelAdmin):
    list_display = ("subject", "tenancy", "channel", "status", "approved_by", "sent_at")
    list_filter = ("status", "channel")


@admin.register(ArrearsCase)
class ArrearsCaseAdmin(admin.ModelAdmin):
    list_display = ("tenancy", "state", "days_overdue", "amount_owing_cents", "opened_at")
    list_filter = ("state",)
