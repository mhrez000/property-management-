from rest_framework import serializers, viewsets

from common.drf import OrgScopedViewMixin
from common.permissions import OrgRolePermission
from payments.models import IncomingPayment, TenancyPaymentReference


class TenancyPaymentReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenancyPaymentReference
        fields = ["id", "tenancy", "trust_account", "provider", "reference", "payid", "is_active"]
        read_only_fields = ["reference", "payid"]


class TenancyPaymentReferenceViewSet(OrgScopedViewMixin, viewsets.ModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = TenancyPaymentReference.objects.all().order_by("-created_at")
    serializer_class = TenancyPaymentReferenceSerializer
    filterset_fields = ["tenancy", "is_active"]

    def perform_create(self, serializer):
        serializer.save(organisation_id=self.get_organisation_id())


class IncomingPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncomingPayment
        fields = [
            "id",
            "provider",
            "provider_txn_id",
            "reference",
            "amount_cents",
            "paid_at",
            "status",
            "receipt",
        ]


class IncomingPaymentViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = IncomingPayment.objects.all().order_by("-paid_at")
    serializer_class = IncomingPaymentSerializer
    filterset_fields = ["status", "provider"]
