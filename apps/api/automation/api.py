from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from rest_framework.response import Response

from common.drf import OrgScopedViewMixin
from common.permissions import OrgRolePermission
from automation.models import (
    ArrearsCase,
    AutomationRule,
    Communication,
    CommunicationTemplate,
    OpsTask,
)


class AutomationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationRule
        fields = ["id", "name", "trigger", "conditions", "actions", "is_active", "created_at"]

    def validate_actions(self, value):
        try:
            AutomationRule(actions=value).clean()
        except Exception as exc:
            raise DRFValidationError(str(exc))
        return value


class AutomationRuleViewSet(OrgScopedViewMixin, viewsets.ModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = AutomationRule.objects.all().order_by("-created_at")
    serializer_class = AutomationRuleSerializer
    filterset_fields = ["trigger", "is_active"]

    def perform_create(self, serializer):
        serializer.save(organisation_id=self.get_organisation_id())


class OpsTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = OpsTask
        fields = [
            "id",
            "title",
            "description",
            "tenancy",
            "assigned_to",
            "due_date",
            "status",
            "created_by_rule",
            "created_at",
        ]
        read_only_fields = ["created_by_rule"]


class OpsTaskViewSet(OrgScopedViewMixin, viewsets.ModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = OpsTask.objects.all().order_by("-created_at")
    serializer_class = OpsTaskSerializer
    filterset_fields = ["status", "tenancy", "assigned_to"]

    def perform_create(self, serializer):
        serializer.save(organisation_id=self.get_organisation_id())


class CommunicationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunicationTemplate
        fields = ["id", "name", "channel", "subject", "body", "created_at"]


class CommunicationTemplateViewSet(OrgScopedViewMixin, viewsets.ModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = CommunicationTemplate.objects.all().order_by("name")
    serializer_class = CommunicationTemplateSerializer


class CommunicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Communication
        fields = [
            "id",
            "tenancy",
            "channel",
            "recipient",
            "subject",
            "body",
            "status",
            "created_by_rule",
            "approved_by",
            "sent_at",
            "created_at",
        ]
        read_only_fields = ["status", "created_by_rule", "approved_by", "sent_at"]


class CommunicationViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    """Drafts are created by the automation engine; humans approve/reject
    here. Sending is a separate explicit step (delivery integration lands
    later), and only ever after approval."""

    permission_classes = [OrgRolePermission]
    queryset = Communication.objects.all().order_by("-created_at")
    serializer_class = CommunicationSerializer
    filterset_fields = ["status", "tenancy"]

    def _transition(self, comm, new_status, *, stamp_approver=False):
        if not self.membership.can_write:
            raise PermissionDenied("Approving communications requires a write-capable role.")
        try:
            comm.transition_to(new_status)
        except Exception as exc:
            raise DRFValidationError(str(exc))
        if stamp_approver:
            comm.approved_by = self.request.user
        comm.save()
        return Response(CommunicationSerializer(comm).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, version=None, pk=None):
        return self._transition(self.get_object(), Communication.Status.APPROVED, stamp_approver=True)

    @action(detail=True, methods=["post"])
    def reject(self, request, version=None, pk=None):
        return self._transition(self.get_object(), Communication.Status.REJECTED, stamp_approver=True)

    @action(detail=True, methods=["post"])
    def send(self, request, version=None, pk=None):
        comm = self.get_object()
        response = self._transition(comm, Communication.Status.SENT)
        comm.sent_at = timezone.now()
        comm.save(update_fields=["sent_at", "updated_at"])
        return response


class ArrearsCaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArrearsCase
        fields = [
            "id",
            "tenancy",
            "state",
            "days_overdue",
            "amount_owing_cents",
            "opened_at",
            "resolved_at",
        ]


class ArrearsCaseViewSet(OrgScopedViewMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [OrgRolePermission]
    queryset = ArrearsCase.objects.all().order_by("-opened_at")
    serializer_class = ArrearsCaseSerializer
    filterset_fields = ["state", "tenancy"]
