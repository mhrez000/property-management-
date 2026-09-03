from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from common.drf import OrgScopedViewMixin
from common.permissions import OrgRolePermission
from ledger.disbursements import (
    approve_disbursement_run,
    create_disbursement_run,
    execute_disbursement_run,
)
from ledger.models import DisbursementLine, DisbursementRun, TrustAccount


class DisbursementLineSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)

    class Meta:
        model = DisbursementLine
        fields = ["id", "owner", "owner_name", "amount_cents", "status", "entry"]


class DisbursementRunSerializer(serializers.ModelSerializer):
    lines = DisbursementLineSerializer(many=True, read_only=True)
    total_cents = serializers.SerializerMethodField()

    class Meta:
        model = DisbursementRun
        fields = [
            "id",
            "trust_account",
            "period_end",
            "state",
            "created_by",
            "approved_by",
            "executed_at",
            "total_cents",
            "lines",
            "created_at",
        ]
        read_only_fields = ["state", "created_by", "approved_by", "executed_at"]

    def get_total_cents(self, obj) -> int:
        return sum(line.amount_cents for line in obj.lines.all())


class CreateRunSerializer(serializers.Serializer):
    trust_account = serializers.PrimaryKeyRelatedField(queryset=TrustAccount.objects.all())
    period_end = serializers.DateField()


class DisbursementRunViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    """Month-end owner payouts: draft → approve → execute.

    Every step that leads to money movement requires a finance/admin role;
    execution only ever follows an explicit approval.
    """

    permission_classes = [OrgRolePermission]
    queryset = DisbursementRun.objects.prefetch_related("lines__owner").order_by("-created_at")
    serializer_class = DisbursementRunSerializer
    filterset_fields = ["state", "trust_account"]

    def _require_money_role(self):
        if not self.membership.can_move_money:
            raise PermissionDenied("Disbursement runs require a finance or admin role.")

    def create(self, request, *args, version=None, **kwargs):
        self._require_money_role()
        serializer = CreateRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = create_disbursement_run(
            trust_account=serializer.validated_data["trust_account"],
            period_end=serializer.validated_data["period_end"],
            created_by=request.user,
        )
        return Response(DisbursementRunSerializer(run).data, status=201)

    @action(detail=True, methods=["post"])
    def approve(self, request, version=None, pk=None):
        self._require_money_role()
        try:
            run = approve_disbursement_run(self.get_object(), approved_by=request.user)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(DisbursementRunSerializer(run).data)

    @action(detail=True, methods=["post"])
    def execute(self, request, version=None, pk=None):
        self._require_money_role()
        try:
            run = execute_disbursement_run(self.get_object(), executed_by=request.user)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(DisbursementRunSerializer(run).data)
