"""The automation engine: outbox processing, arrears evaluation, actions."""

from __future__ import annotations

import logging
from datetime import date, datetime
from string import Template

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from automation.models import (
    ArrearsCase,
    AutomationRule,
    Communication,
    CommunicationTemplate,
    OpsTask,
    OutboxEvent,
    RuleFiring,
)
from common.tenancy import bypass_rls, org_context
from portfolio.models import RentCharge, Tenancy
from portfolio.services import arrears_for_tenancy

logger = logging.getLogger(__name__)


def emit_event(organisation_id, event_type: str, payload: dict, occurred_at: datetime | None = None):
    """Write a domain event in the CURRENT transaction (transactional outbox).

    Call inside the same ``transaction.atomic`` block as the state change the
    event describes; the event only exists if the change commits.
    """
    return OutboxEvent.objects.create(
        organisation_id=organisation_id,
        event_type=event_type,
        payload=payload,
        occurred_at=occurred_at or timezone.now(),
    )


# ---------------------------------------------------------------------------
# Conditions & actions
# ---------------------------------------------------------------------------

def conditions_match(conditions: dict, context: dict) -> bool:
    """All configured conditions must hold (AND semantics)."""
    if not conditions:
        return True
    days = context.get("days_overdue", 0)
    amount = context.get("amount_owing_cents", 0)
    if "min_days_overdue" in conditions and days < conditions["min_days_overdue"]:
        return False
    if "max_days_overdue" in conditions and days > conditions["max_days_overdue"]:
        return False
    if "min_amount_cents" in conditions and amount < conditions["min_amount_cents"]:
        return False
    return True


def _template_context(tenancy: Tenancy, context: dict) -> dict:
    return {
        "tenant_names": ", ".join(tenancy.tenants.values_list("name", flat=True)),
        "tenancy_reference": tenancy.reference,
        "property_address": str(tenancy.property),
        "days_overdue": context.get("days_overdue", 0),
        # Includes the currency symbol so templates read "$amount_owing"
        # without wrestling string.Template's `$$` escaping.
        "amount_owing": f"${context.get('amount_owing_cents', 0) / 100:.2f}",
    }


def execute_actions(rule: AutomationRule, tenancy: Tenancy | None, context: dict) -> list:
    """Run a rule's actions. Never sends anything, never moves money —
    outputs are tasks, draft communications, and arrears-state changes."""
    results = []
    for action in rule.actions:
        kind = action.get("type")
        if kind == "create_task":
            assigned = None
            if tenancy is not None and action.get("assign_to_manager"):
                assigned = tenancy.property.assigned_manager
            results.append(
                OpsTask.objects.create(
                    organisation_id=rule.organisation_id,
                    title=Template(action.get("title", rule.name)).safe_substitute(
                        _template_context(tenancy, context) if tenancy else {}
                    ),
                    description=action.get("description", ""),
                    tenancy=tenancy,
                    assigned_to=assigned,
                    created_by_rule=rule,
                )
            )
        elif kind == "draft_communication":
            template = CommunicationTemplate.objects.filter(
                organisation_id=rule.organisation_id, name=action.get("template", "")
            ).first()
            if template is None:
                logger.warning("Rule %s references missing template %r", rule.pk, action.get("template"))
                continue
            ctx = _template_context(tenancy, context) if tenancy else {}
            recipient = ""
            if tenancy is not None:
                first_tenant = tenancy.tenants.first()
                if first_tenant is not None:
                    recipient = first_tenant.email or first_tenant.phone
            results.append(
                Communication.objects.create(
                    organisation_id=rule.organisation_id,
                    tenancy=tenancy,
                    channel=template.channel,
                    recipient=recipient,
                    subject=Template(template.subject).safe_substitute(ctx),
                    body=Template(template.body).safe_substitute(ctx),
                    created_by_rule=rule,
                )
            )
        elif kind == "escalate_arrears":
            if tenancy is None:
                continue
            case = ArrearsCase.objects.filter(tenancy=tenancy).exclude(
                state=ArrearsCase.State.RESOLVED
            ).first()
            if case is None:
                continue
            try:
                case.escalate_to(action.get("to_state", ArrearsCase.State.ESCALATED))
                case.save(update_fields=["state", "updated_at"])
                results.append(case)
            except ValidationError:
                # Already at or beyond the target state — nothing to do.
                pass
    return results


def _fire_once(rule: AutomationRule, tenancy: Tenancy | None, dedup_key: str) -> bool:
    """Record a firing; returns False if this situation already fired."""
    _, created = RuleFiring.objects.get_or_create(
        rule=rule,
        tenancy=tenancy,
        dedup_key=dedup_key,
        defaults={"organisation_id": rule.organisation_id, "fired_at": timezone.now()},
    )
    return created


# ---------------------------------------------------------------------------
# Outbox processing (event-based triggers)
# ---------------------------------------------------------------------------

def process_outbox(limit: int = 200) -> int:
    """Process unprocessed outbox events. Safe to run concurrently
    (row locks with skip_locked) and repeatedly (processed_at guard)."""
    with bypass_rls():
        event_ids = list(
            OutboxEvent.objects.filter(processed_at__isnull=True)
            .order_by("occurred_at")
            .values_list("id", flat=True)[:limit]
        )
    handled = 0
    for event_id in event_ids:
        with bypass_rls():
            org_id = (
                OutboxEvent.objects.filter(pk=event_id)
                .values_list("organisation_id", flat=True)
                .first()
            )
        if org_id is None:
            continue
        with org_context(org_id):
            try:
                with transaction.atomic():
                    event = (
                        OutboxEvent.objects.select_for_update(skip_locked=True)
                        .filter(pk=event_id, processed_at__isnull=True)
                        .first()
                    )
                    if event is None:
                        continue
                    _handle_event(event)
                    event.processed_at = timezone.now()
                    event.attempts += 1
                    event.save(update_fields=["processed_at", "attempts", "updated_at"])
                    handled += 1
            except Exception as exc:  # keep the batch going; record the failure
                logger.exception("Outbox event %s failed", event_id)
                OutboxEvent.objects.filter(pk=event_id).update(
                    attempts=F("attempts") + 1, last_error=str(exc)[:2000]
                )
    return handled


def _handle_event(event: OutboxEvent) -> None:
    tenancy = None
    tenancy_id = event.payload.get("tenancy_id")
    if tenancy_id:
        tenancy = Tenancy.objects.filter(pk=tenancy_id).first()

    if event.event_type == OutboxEvent.Type.RENT_RECEIVED and tenancy is not None:
        resolve_arrears_if_paid(tenancy)

    context = {
        "days_overdue": event.payload.get("days_overdue", 0),
        "amount_owing_cents": event.payload.get("amount_owing_cents", 0),
    }
    rules = AutomationRule.objects.filter(trigger=event.event_type, is_active=True)
    for rule in rules:
        if not conditions_match(rule.conditions, context):
            continue
        if not _fire_once(rule, tenancy, dedup_key=f"event:{event.pk}"):
            continue
        execute_actions(rule, tenancy, context)


# ---------------------------------------------------------------------------
# Scheduled arrears evaluation
# ---------------------------------------------------------------------------

def resolve_arrears_if_paid(tenancy: Tenancy, as_of: date | None = None) -> bool:
    """Close the open arrears case when nothing due remains unpaid."""
    as_of = as_of or timezone.localdate()
    summary = arrears_for_tenancy(tenancy.pk, as_of=as_of)
    if summary["total_owing_cents"] > 0:
        return False
    updated = (
        ArrearsCase.objects.filter(tenancy=tenancy)
        .exclude(state=ArrearsCase.State.RESOLVED)
        .update(
            state=ArrearsCase.State.RESOLVED,
            resolved_at=timezone.now(),
            days_overdue=0,
            amount_owing_cents=0,
        )
    )
    return bool(updated)


def evaluate_arrears_for_org(organisation_id, as_of: date | None = None) -> int:
    """Daily pass for one organisation: refresh arrears cases and fire
    ``rent_overdue`` rules. Must run inside that org's tenant context."""
    as_of = as_of or timezone.localdate()
    fired = 0

    overdue_tenancy_ids = (
        RentCharge.objects.filter(due_date__lte=as_of)
        .exclude(status=RentCharge.Status.PAID)
        .values_list("tenancy_id", flat=True)
        .distinct()
    )
    rules = list(AutomationRule.objects.filter(trigger=AutomationRule.Trigger.RENT_OVERDUE, is_active=True))

    for tenancy in Tenancy.objects.filter(pk__in=list(overdue_tenancy_ids)):
        summary = arrears_for_tenancy(tenancy.pk, as_of=as_of)
        oldest_due = (
            RentCharge.objects.filter(tenancy=tenancy, due_date__lte=as_of)
            .exclude(status=RentCharge.Status.PAID)
            .order_by("due_date")
            .values_list("due_date", flat=True)
            .first()
        )
        case = (
            ArrearsCase.objects.filter(tenancy=tenancy)
            .exclude(state=ArrearsCase.State.RESOLVED)
            .first()
        )
        if case is None:
            case = ArrearsCase.objects.create(
                organisation_id=tenancy.organisation_id,
                tenancy=tenancy,
                state=ArrearsCase.State.OVERDUE,
                opened_at=timezone.now(),
            )
        case.days_overdue = summary["days_overdue"]
        case.amount_owing_cents = summary["total_owing_cents"]
        case.save(update_fields=["days_overdue", "amount_owing_cents", "updated_at"])

        context = {
            "days_overdue": summary["days_overdue"],
            "amount_owing_cents": summary["total_owing_cents"],
        }
        for rule in rules:
            if not conditions_match(rule.conditions, context):
                continue
            # One firing per rule per arrears episode (keyed on the oldest
            # unpaid due date): the evaluator can run daily without spamming.
            if not _fire_once(rule, tenancy, dedup_key=f"overdue:{oldest_due.isoformat()}"):
                continue
            execute_actions(rule, tenancy, context)
            fired += 1

    # Tenancies whose case is open but who are now paid up.
    for case in ArrearsCase.objects.exclude(state=ArrearsCase.State.RESOLVED).exclude(
        tenancy_id__in=list(overdue_tenancy_ids)
    ):
        resolve_arrears_if_paid(case.tenancy, as_of=as_of)

    return fired


def evaluate_arrears_all_orgs(as_of: date | None = None) -> int:
    """Entry point for the daily beat task: fan out per organisation."""
    with bypass_rls():
        org_ids = list(Tenancy.objects.values_list("organisation_id", flat=True).distinct())
    total = 0
    for org_id in org_ids:
        with org_context(org_id):
            total += evaluate_arrears_for_org(org_id, as_of=as_of)
    return total
