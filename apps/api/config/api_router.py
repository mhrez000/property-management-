from django.urls import path
from rest_framework.routers import DefaultRouter

from accounts.api import me
from automation.api import (
    ArrearsCaseViewSet,
    AutomationRuleViewSet,
    CommunicationTemplateViewSet,
    CommunicationViewSet,
    OpsTaskViewSet,
)
from compliance.api import ComplianceRuleViewSet
from ledger.api import (
    AccountViewSet,
    JournalEntryViewSet,
    ReceiptViewSet,
    ReconciliationViewSet,
    TrustAccountViewSet,
)
from payments.api import IncomingPaymentViewSet, TenancyPaymentReferenceViewSet
from payments.views import payment_webhook
from portfolio.api import (
    LeaseViewSet,
    OwnerViewSet,
    PropertyViewSet,
    RentChargeViewSet,
    TenancyViewSet,
    TenantViewSet,
)

router = DefaultRouter()
router.register("owners", OwnerViewSet)
router.register("properties", PropertyViewSet)
router.register("tenants", TenantViewSet)
router.register("tenancies", TenancyViewSet)
router.register("leases", LeaseViewSet)
router.register("rent-charges", RentChargeViewSet)
router.register("trust-accounts", TrustAccountViewSet)
router.register("ledger/accounts", AccountViewSet, basename="ledger-account")
router.register("ledger/entries", JournalEntryViewSet, basename="ledger-entry")
router.register("ledger/receipts", ReceiptViewSet, basename="ledger-receipt")
router.register("ledger/reconciliations", ReconciliationViewSet, basename="ledger-reconciliation")
router.register("payments/references", TenancyPaymentReferenceViewSet)
router.register("payments/incoming", IncomingPaymentViewSet)
router.register("compliance/rules", ComplianceRuleViewSet)
router.register("automation/rules", AutomationRuleViewSet)
router.register("automation/tasks", OpsTaskViewSet)
router.register("automation/templates", CommunicationTemplateViewSet)
router.register("automation/communications", CommunicationViewSet)
router.register("automation/arrears", ArrearsCaseViewSet)

urlpatterns = router.urls + [
    path("me/", me, name="me"),
    path("payments/webhooks/<str:provider_name>/", payment_webhook, name="payment-webhook"),
]
