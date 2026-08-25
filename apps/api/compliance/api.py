from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated

from compliance.models import ComplianceRule


class ComplianceRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceRule
        fields = [
            "id",
            "jurisdiction",
            "rule_type",
            "effective_from",
            "effective_to",
            "config",
            "source",
            "verified",
        ]


class ComplianceRuleViewSet(viewsets.ReadOnlyModelViewSet):
    """Global reference data — readable by any authenticated user."""

    permission_classes = [IsAuthenticated]
    queryset = ComplianceRule.objects.all().order_by("jurisdiction", "rule_type", "effective_from")
    serializer_class = ComplianceRuleSerializer
    filterset_fields = ["jurisdiction", "rule_type"]
    pagination_class = None
