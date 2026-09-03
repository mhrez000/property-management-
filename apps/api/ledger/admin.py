from django.contrib import admin

from ledger.models import (
    Account,
    BankTransaction,
    DisbursementLine,
    DisbursementRun,
    JournalEntry,
    Posting,
    Receipt,
    Reconciliation,
    TrustAccount,
)


class DisbursementLineInline(admin.TabularInline):
    model = DisbursementLine
    extra = 0
    readonly_fields = ("owner", "amount_cents", "status", "entry")


@admin.register(DisbursementRun)
class DisbursementRunAdmin(admin.ModelAdmin):
    list_display = ("period_end", "trust_account", "state", "approved_by", "executed_at")
    list_filter = ("state",)
    inlines = [DisbursementLineInline]


class PostingInline(admin.TabularInline):
    model = Posting
    extra = 0
    can_delete = False
    readonly_fields = ("account", "side", "amount_cents")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(TrustAccount)
class TrustAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "bsb", "account_number")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "subtype", "is_active")
    list_filter = ("type", "subtype")


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("posted_at", "description", "external_id")
    inlines = [PostingInline]
    readonly_fields = ("description", "posted_at", "external_id", "reverses", "created_by")

    # Ledger rows are immutable — the admin is a viewer, not an editor.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("number", "trust_account", "amount_cents", "received_from", "issued_at")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "trust_account", "amount_cents", "description", "matched_entry")


@admin.register(Reconciliation)
class ReconciliationAdmin(admin.ModelAdmin):
    list_display = (
        "cutoff_date",
        "trust_account",
        "bank_balance_cents",
        "cashbook_balance_cents",
        "subledger_total_cents",
        "status",
    )
    list_filter = ("status",)
