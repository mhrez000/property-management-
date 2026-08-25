from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

from accounts.models import Membership, Organisation


class OrganisationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organisation
        fields = ["id", "name", "abn", "timezone"]


class MembershipSerializer(serializers.ModelSerializer):
    organisation = OrganisationSerializer()

    class Meta:
        model = Membership
        fields = ["organisation", "role"]


class MeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    memberships = MembershipSerializer(many=True)


@extend_schema(responses=MeSerializer)
@api_view(["GET"])
def me(request, version=None):
    memberships = request.user.memberships.select_related("organisation")
    return Response(
        {
            "id": str(request.user.id),
            "email": request.user.email,
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "memberships": MembershipSerializer(memberships, many=True).data,
        }
    )
