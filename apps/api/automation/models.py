"""Arrears automation: trigger–condition–action over a transactional outbox.

Design (blueprint §7):

- **Outbox**: domain events are written to ``OutboxEvent`` in the same
  database transaction as the state change that caused them; a worker then
  processes them. Nothing rides on in-process signals, so a crashed worker
  never loses an event.
- **TCA rules**: ``AutomationRule`` rows pair a trigger (an event type, or
  the scheduled arrears evaluator) with JSON conditions and a list of
  actions. Rules are data — agencies tune them without code changes.
- **Guardrails**: actions never move money and never contact a tenant
  directly. Communications are created as drafts requiring explicit human
  approval; legal notices are modelled as an arrears state that a human must
  action. ``RuleFiring`` deduplicates so a daily evaluator can't re-fire the
  same rule for the same situation.
"""

from django.core.exceptions import ValidationError
from django.db import models

from common.models import OrgScopedModel


class OutboxEvent(OrgScopedModel):
    """A domain event, written transactionally, processed asynchronously."""

    class Type(models.TextChoices):
        RENT_RECEIVED = "rent_received", "Rent received"
        PAYMENT_DISHONOURED = "payment_dishonoured", "Payment dishonoured"
        LEASE_ACTIVATED = "lease_activated", "Lease activated"
        ARREARS_THRESHOLD = "arrears_threshold", "Arrears threshold crossed"

    event_type = models.CharField(max_length=64, choices=Type.choices)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["organisation", "occurred_at"],
                condition=models.Q(processed_at__isnull=True),
                name="outbox_unprocessed_idx",
            )
        ]
        ordering = ["occurred_at"]


class AutomationRule(OrgScopedModel):
    """A trigger–condition–action rule.

    ``trigger`` is either an OutboxEvent type or the scheduled
    ``rent_overdue`` trigger evaluated daily against unpaid rent charges.

    ``conditions`` (all optional, AND-ed):
        min_days_overdue / max_days_overdue: int
        min_amount_cents: int

    ``actions`` is a list of ``{"type": ..., ...config}``:
        create_task: {title, description?, assign_to_manager?: bool}
        draft_communication: {template: <CommunicationTemplate name>}
        escalate_arrears: {to_state: <ArrearsCase.State>}
    """

    class Trigger(models.TextChoices):
        RENT_RECEIVED = OutboxEvent.Type.RENT_RECEIVED.value, "Rent received"
        PAYMENT_DISHONOURED = OutboxEvent.Type.PAYMENT_DISHONOURED.value, "Payment dishonoured"
        RENT_OVERDUE = "rent_overdue", "Rent overdue (scheduled check)"

    name = models.CharField(max_length=255)
    trigger = models.CharField(max_length=64, choices=Trigger.choices)
    conditions = models.JSONField(default=dict, blank=True)
    actions = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)

    ACTION_TYPES = {"create_task", "draft_communication", "escalate_arrears"}

    def clean(self):
        if not isinstance(self.actions, list) or not self.actions:
            raise ValidationError("A rule needs at least one action.")
        for action in self.actions:
            if not isinstance(action, dict) or action.get("type") not in self.ACTION_TYPES:
                raise ValidationError(
                    f"Unknown action type {action!r}; allowed: {sorted(self.ACTION_TYPES)}"
                )

    def __str__(self):
        return self.name


class RuleFiring(OrgScopedModel):
    """Dedup record: one firing per (rule, tenancy, situation).

    ``dedup_key`` encodes the situation (e.g. the oldest unpaid due date for
    an arrears rule) so the same episode never re-fires, but a new episode —
    the tenant catches up, then falls behind again — fires afresh.
    """

    rule = models.ForeignKey(AutomationRule, on_delete=models.CASCADE, related_name="firings")
    tenancy = models.ForeignKey(
        "portfolio.Tenancy", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    dedup_key = models.CharField(max_length=255)
    fired_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "tenancy", "dedup_key"], name="uniq_rule_firing"
            )
        ]


class OpsTask(OrgScopedModel):
    """A work item for a property manager (created by rules or by hand)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"
        DISMISSED = "dismissed", "Dismissed"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    tenancy = models.ForeignKey(
        "portfolio.Tenancy", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    assigned_to = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="ops_tasks"
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_by_rule = models.ForeignKey(
        AutomationRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        indexes = [models.Index(fields=["organisation", "status"])]

    def __str__(self):
        return self.title


class CommunicationTemplate(OrgScopedModel):
    """A message template with ``$placeholder`` substitution (string.Template).

    Available context: $tenant_names, $tenancy_reference, $property_address,
    $days_overdue, $amount_owing (rendered with a leading dollar sign,
    e.g. "$1950.00").
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    name = models.CharField(max_length=128)
    channel = models.CharField(max_length=8, choices=Channel.choices, default=Channel.EMAIL)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organisation", "name"], name="uniq_template_name")
        ]

    def __str__(self):
        return self.name


class Communication(OrgScopedModel):
    """A drafted message. ALWAYS requires human approval before sending.

    The automation engine only ever creates drafts; a person approves (or
    rejects) and the send step records who. Actual delivery integration
    (SES/MessageMedia) lands later — ``mark_sent`` is the seam.
    """

    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        SENT = "sent", "Sent"
        REJECTED = "rejected", "Rejected"

    ALLOWED_TRANSITIONS = {
        Status.PENDING_APPROVAL: {Status.APPROVED, Status.REJECTED},
        Status.APPROVED: {Status.SENT},
        Status.SENT: set(),
        Status.REJECTED: set(),
    }

    tenancy = models.ForeignKey(
        "portfolio.Tenancy",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="communications",
    )
    channel = models.CharField(max_length=8, choices=CommunicationTemplate.Channel.choices)
    recipient = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDING_APPROVAL
    )
    created_by_rule = models.ForeignKey(
        AutomationRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organisation", "status"])]

    def transition_to(self, new_status: str):
        allowed = self.ALLOWED_TRANSITIONS[self.Status(self.status)]
        if self.Status(new_status) not in allowed:
            raise ValidationError(
                f"Invalid communication transition {self.status} → {new_status}"
            )
        self.status = new_status


class ArrearsCase(OrgScopedModel):
    """The arrears episode for a tenancy, driven by the daily evaluator.

    ``NOTICE_PENDING`` deliberately means "a human must prepare/issue the
    notice" — the system never auto-issues a termination or breach notice.
    """

    class State(models.TextChoices):
        MONITORING = "monitoring", "Monitoring"
        OVERDUE = "overdue", "Overdue"
        ESCALATED = "escalated", "Escalated"
        NOTICE_PENDING = "notice_pending", "Notice pending (human action)"
        RESOLVED = "resolved", "Resolved"

    # Forward-only while open; any open state can resolve when paid up.
    ORDER = [State.MONITORING, State.OVERDUE, State.ESCALATED, State.NOTICE_PENDING]

    tenancy = models.ForeignKey(
        "portfolio.Tenancy", on_delete=models.CASCADE, related_name="arrears_cases"
    )
    state = models.CharField(max_length=32, choices=State.choices, default=State.OVERDUE)
    days_overdue = models.IntegerField(default=0)
    amount_owing_cents = models.BigIntegerField(default=0)
    opened_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenancy"],
                condition=~models.Q(state="resolved"),
                name="uniq_open_arrears_case_per_tenancy",
            )
        ]

    def escalate_to(self, new_state: str):
        new = self.State(new_state)
        if new == self.State.RESOLVED:
            raise ValidationError("Use resolve(); escalate_to is for open states.")
        if self.state == self.State.RESOLVED:
            raise ValidationError("Case is resolved; a new episode opens a new case.")
        if self.ORDER.index(new) <= self.ORDER.index(self.State(self.state)):
            raise ValidationError(
                f"Arrears escalation must move forward ({self.state} → {new_state} rejected)."
            )
        self.state = new
