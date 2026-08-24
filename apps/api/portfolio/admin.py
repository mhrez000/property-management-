from django.contrib import admin

from portfolio.models import (
    Lease,
    Owner,
    OwnerProperty,
    Property,
    RentCharge,
    RentSchedule,
    Tenancy,
    Tenant,
)


class OwnerPropertyInline(admin.TabularInline):
    model = OwnerProperty
    extra = 0


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("address_line_1", "suburb", "state", "postcode", "is_active")
    list_filter = ("state", "is_active")
    search_fields = ("address_line_1", "suburb", "postcode")
    inlines = [OwnerPropertyInline]


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone")
    search_fields = ("name", "email")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone")
    search_fields = ("name", "email")


@admin.register(Tenancy)
class TenancyAdmin(admin.ModelAdmin):
    list_display = ("reference", "property")
    search_fields = ("reference",)


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("tenancy", "state", "start_date", "end_date", "rent_amount_cents")
    list_filter = ("state",)


@admin.register(RentSchedule)
class RentScheduleAdmin(admin.ModelAdmin):
    list_display = ("lease", "amount_cents", "frequency", "first_due_date", "end_date")


@admin.register(RentCharge)
class RentChargeAdmin(admin.ModelAdmin):
    list_display = ("tenancy", "due_date", "amount_cents", "paid_cents", "status")
    list_filter = ("status",)
