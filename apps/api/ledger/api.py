from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from common.drf import OrgScopedViewMixin
from common.permissions import OrgRolePermission
from ledger.models import Account, JournalEntry, Posting, Receipt, Reconciliation, TrustAccount
from ledger.services import account_balance_cents, run_three_way_reconciliation


class TrustAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrustAccount
        fields = ["id", "name", "bsb", "account_number", "created_at"]


class TrustAccountViewSet(OrgScopedViewMixin, viewsets.ModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = TrustAccount.objects.all().order_by("created_at")
    serializer_class = TrustAccountSerializer

    def perform_create(self, serializer):
        serializer.save(organisation_id=self.get_organisation_id())

    @action(detail=True, methods=["post"], url_path="reconcile")
    def reconcile(self, request, version=None, pk=None):
        """Run a three-way reconciliation at a cut-off date (finance roles only)."""
        if not self.membership.can_move_money:
            raise PermissionDenied("Reconciliation requires a finance or admin role.")
        serializer = ReconcileRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recon = run_three_way_reconciliation(
            trust_account=self.get_object(),
            cutoff_date=serializer.validated_data["cutoff_date"],
            prepared_by=request.user,
        )
        return Response(ReconciliationSerializer(recon).data, status=201)


class ReconcileRequestSerializer(serializers.Serializer):
    cutoff_date = serializers.DateField()


class AccountSerializer(serializers.ModelSerializer):
    balance_cents = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = ["id", "name", "type", "subtype", "trust_account", "owner", "tenancy", "balance_cents"]

    def get_balance_cents(self, obj) -> int:
        return account_balance_cents(obj)


class AccountViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = Account.objects.all().order_by("created_at")
    serializer_class = AccountSerializer
    filterset_fields = ["type", "subtype"]


class PostingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Posting
        fields = ["account", "side", "amount_cents"]


class JournalEntrySerializer(serializers.ModelSerializer):
    postings = PostingSerializer(many=True, read_only=True)

    class Meta:
        model = JournalEntry
        fields = ["id", "description", "posted_at", "external_id", "reverses", "postings"]


class JournalEntryViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    """The ledger is read-only over the API: entries are only created via
    domain services (receipting, disbursement), never free-form."""

    permission_classes = [OrgRolePermission]
    queryset = JournalEntry.objects.prefetch_related("postings").order_by("-posted_at")
    serializer_class = JournalEntrySerializer


class ReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Receipt
        fields = [
            "id",
            "trust_account",
            "number",
            "journal_entry",
            "amount_cents",
            "received_from",
            "method",
            "issued_at",
        ]


class ReceiptViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = Receipt.objects.all().order_by("-number")
    serializer_class = ReceiptSerializer
    filterset_fields = ["trust_account"]


class ReconciliationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reconciliation
        fields = [
            "id",
            "trust_account",
            "cutoff_date",
            "bank_balance_cents",
            "cashbook_balance_cents",
            "subledger_total_cents",
            "status",
            "created_at",
        ]


class ReconciliationViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = Reconciliation.objects.all().order_by("-cutoff_date")
    serializer_class = ReconciliationSerializer
    filterset_fields = ["trust_account", "status"]
