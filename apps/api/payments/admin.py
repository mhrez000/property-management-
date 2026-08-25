from django.contrib import admin

from payments.models import IncomingPayment, TenancyPaymentReference


@admin.register(TenancyPaymentReference)
class TenancyPaymentReferenceAdmin(admin.ModelAdmin):
    list_display = ("reference", "tenancy", "trust_account", "provider", "is_active")
    list_filter = ("provider", "is_active")
    search_fields = ("reference",)


@admin.register(IncomingPayment)
class IncomingPaymentAdmin(admin.ModelAdmin):
    list_display = ("provider", "provider_txn_id", "reference", "amount_cents", "paid_at", "status")
    list_filter = ("provider", "status")

    def has_change_permission(self, request, obj=None):
        return False
