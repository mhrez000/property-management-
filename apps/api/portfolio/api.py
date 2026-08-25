from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from common.drf import OrgScopedViewMixin
from common.permissions import OrgRolePermission
from portfolio.models import Lease, Owner, Property, RentCharge, Tenancy, Tenant


class OrgScopedModelViewSet(OrgScopedViewMixin, viewsets.ModelViewSet):
    permission_classes = [OrgRolePermission]

    def perform_create(self, serializer):
        serializer.save(organisation_id=self.get_organisation_id())


class OwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Owner
        fields = ["id", "name", "email", "phone", "bank_account_label", "created_at"]


class OwnerViewSet(OrgScopedModelViewSet):
    queryset = Owner.objects.all().order_by("-created_at")
    serializer_class = OwnerSerializer
    filterset_fields = ["name"]


class PropertySerializer(serializers.ModelSerializer):
    class Meta:
        model = Property
        fields = [
            "id",
            "address_line_1",
            "address_line_2",
            "suburb",
            "state",
            "postcode",
            "bedrooms",
            "bathrooms",
            "car_spaces",
            "assigned_manager",
            "is_active",
            "management_fee_bps",
            "created_at",
        ]


class PropertyViewSet(OrgScopedModelViewSet):
    queryset = Property.objects.all().order_by("-created_at")
    serializer_class = PropertySerializer
    filterset_fields = ["state", "suburb", "is_active"]


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "email", "phone", "created_at"]


class TenantViewSet(OrgScopedModelViewSet):
    queryset = Tenant.objects.all().order_by("-created_at")
    serializer_class = TenantSerializer


class TenancySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenancy
        fields = ["id", "property", "tenants", "reference", "created_at"]


class TenancyViewSet(OrgScopedModelViewSet):
    queryset = Tenancy.objects.all().order_by("-created_at")
    serializer_class = TenancySerializer
    filterset_fields = ["property"]

    @action(detail=True, methods=["post"])
    def allocate(self, request, version=None, pk=None):
        """Allocate the tenancy sub-ledger balance to its owners (net of the
        property's management fee). Finance/admin roles only."""
        if not self.membership.can_move_money:
            raise PermissionDenied("Allocations require a finance or admin role.")
        from ledger.disbursements import allocate_tenancy_funds

        entries = allocate_tenancy_funds(self.get_object(), created_by=request.user)
        return Response({"entries_posted": len(entries)})


class LeaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lease
        fields = [
            "id",
            "tenancy",
            "state",
            "start_date",
            "end_date",
            "rent_amount_cents",
            "rent_frequency",
            "bond_amount_cents",
            "created_at",
        ]
        read_only_fields = ["state"]


class LeaseViewSet(OrgScopedModelViewSet):
    queryset = Lease.objects.all().order_by("-created_at")
    serializer_class = LeaseSerializer
    filterset_fields = ["state", "tenancy"]


class RentChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RentCharge
        fields = [
            "id",
            "tenancy",
            "schedule",
            "due_date",
            "period_start",
            "period_end",
            "amount_cents",
            "paid_cents",
            "status",
        ]


class RentChargeViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = RentCharge.objects.all().order_by("due_date")
    serializer_class = RentChargeSerializer
    filterset_fields = ["tenancy", "status"]
