from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from common.drf import OrgScopedViewMixin
from common.permissions import OrgRolePermission
from compliance.bonds import create_bond_lodgement, mark_bond_lodged, receive_bond
from compliance.models_bonds import BondLodgement
from ledger.models import TrustAccount
from portfolio.models import Lease


class BondLodgementSerializer(serializers.ModelSerializer):
    class Meta:
        model = BondLodgement
        fields = [
            "id",
            "lease",
            "amount_cents",
            "jurisdiction",
            "authority_name",
            "held_in_agent_trust",
            "state",
            "trust_account",
            "received_on",
            "due_date",
            "authority_reference",
            "lodged_at",
            "created_at",
        ]
        read_only_fields = [f for f in fields if f not in ("lease", "amount_cents")]


class BondReceiveSerializer(serializers.Serializer):
    trust_account = serializers.PrimaryKeyRelatedField(queryset=TrustAccount.objects.all())
    external_id = serializers.CharField(max_length=255)
    received_on = serializers.DateField(required=False)


class BondLodgeSerializer(serializers.Serializer):
    authority_reference = serializers.CharField(max_length=128)
    external_id = serializers.CharField(max_length=255)


class BondLodgementViewSet(OrgScopedViewMixin, viewsets.ModelViewSet):
    """Bond workflow: create → receive (money into trust) → lodge.

    The money-moving steps require a finance/admin role.
    """

    permission_classes = [OrgRolePermission]
    queryset = BondLodgement.objects.all().order_by("-created_at")
    serializer_class = BondLodgementSerializer
    filterset_fields = ["state", "jurisdiction"]
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        lease = Lease.objects.filter(pk=request.data.get("lease")).first()
        if lease is None:
            raise DRFValidationError({"lease": "Unknown lease."})
        amount = request.data.get("amount_cents")
        try:
            bond = create_bond_lodgement(
                lease, amount_cents=int(amount) if amount is not None else None
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(BondLodgementSerializer(bond).data, status=201)

    def _require_money_role(self):
        if not self.membership.can_move_money:
            raise PermissionDenied("Bond money movements require a finance or admin role.")

    @action(detail=True, methods=["post"])
    def receive(self, request, version=None, pk=None):
        self._require_money_role()
        serializer = BondReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            bond = receive_bond(
                self.get_object(),
                trust_account=serializer.validated_data["trust_account"],
                external_id=serializer.validated_data["external_id"],
                received_on=serializer.validated_data.get("received_on"),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(BondLodgementSerializer(bond).data)

    @action(detail=True, methods=["post"])
    def lodge(self, request, version=None, pk=None):
        self._require_money_role()
        serializer = BondLodgeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            bond = mark_bond_lodged(
                self.get_object(),
                authority_reference=serializer.validated_data["authority_reference"],
                external_id=serializer.validated_data["external_id"],
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(BondLodgementSerializer(bond).data)
