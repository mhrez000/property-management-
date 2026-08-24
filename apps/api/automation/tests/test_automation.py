"""Automation engine tests: outbox reliability, TCA rules, guardrails.

The load-bearing assertions:

- the daily arrears evaluator fires each rule once per arrears episode
  (no daily spam), creates ONLY drafts and tasks — nothing is auto-sent;
- a rent payment (via the outbox event) resolves the arrears case;
- communication drafts demand explicit human approval before sending, and
  role checks apply.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Membership, User
from automation.models import (
    ArrearsCase,
    AutomationRule,
    Communication,
    CommunicationTemplate,
    OpsTask,
    OutboxEvent,
)
from automation.services import (
    conditions_match,
    emit_event,
    evaluate_arrears_for_org,
    process_outbox,
)
from common.tenancy import org_context
from portfolio.models import Lease, RentCharge, RentSchedule
from portfolio.services import apply_receipt_to_charges, generate_rent_charges

pytestmark = pytest.mark.django_db


@pytest.fixture
def overdue_tenancy(org, tenancy):
    """A tenancy 3 weeks in arrears (charges due 1/8/15 Jan, unpaid)."""
    with org_context(org.pk):
        lease = Lease.objects.create(
            organisation=org,
            tenancy=tenancy,
            state=Lease.State.ACTIVE,
            start_date=date(2026, 1, 1),
            rent_amount_cents=65_000,
            rent_frequency="weekly",
        )
        schedule = RentSchedule.objects.create(
            organisation=org,
            lease=lease,
            amount_cents=65_000,
            frequency="weekly",
            first_due_date=date(2026, 1, 1),
        )
        generate_rent_charges(schedule, until=date(2026, 1, 15))
        yield tenancy


@pytest.fixture
def arrears_rule(org):
    with org_context(org.pk):
        CommunicationTemplate.objects.create(
            organisation=org,
            name="arrears-reminder",
            channel="email",
            subject="Rent overdue for $property_address",
            body="Hi $tenant_names, your rent is $days_overdue days overdue "
            "(total $amount_owing). Reference: $tenancy_reference.",
        )
        yield AutomationRule.objects.create(
            organisation=org,
            name="14-day arrears escalation",
            trigger=AutomationRule.Trigger.RENT_OVERDUE,
            conditions={"min_days_overdue": 14},
            actions=[
                {"type": "draft_communication", "template": "arrears-reminder"},
                {"type": "create_task", "title": "Call tenant re arrears — $tenancy_reference"},
                {"type": "escalate_arrears", "to_state": "escalated"},
            ],
        )


class TestConditions:
    def test_and_semantics(self):
        conditions = {"min_days_overdue": 7, "min_amount_cents": 10_000}
        assert conditions_match(conditions, {"days_overdue": 10, "amount_owing_cents": 20_000})
        assert not conditions_match(conditions, {"days_overdue": 3, "amount_owing_cents": 20_000})
        assert not conditions_match(conditions, {"days_overdue": 10, "amount_owing_cents": 5_000})
        assert conditions_match({}, {"days_overdue": 0})

    def test_max_days_window(self):
        conditions = {"min_days_overdue": 7, "max_days_overdue": 13}
        assert conditions_match(conditions, {"days_overdue": 10})
        assert not conditions_match(conditions, {"days_overdue": 20})


class TestArrearsEvaluator:
    AS_OF = date(2026, 1, 20)  # oldest charge (1 Jan) is 19 days overdue

    def test_fires_rule_creates_draft_task_and_escalates(self, org, overdue_tenancy, arrears_rule):
        with org_context(org.pk):
            fired = evaluate_arrears_for_org(org.pk, as_of=self.AS_OF)
            assert fired == 1

            case = ArrearsCase.objects.get()
            assert case.state == ArrearsCase.State.ESCALATED
            assert case.days_overdue == 19
            assert case.amount_owing_cents == 195_000

            comm = Communication.objects.get()
            assert comm.status == Communication.Status.PENDING_APPROVAL  # never auto-sent
            assert comm.sent_at is None
            assert "19 days overdue" in comm.body
            assert "$1950.00" in comm.body
            assert "TEN-0001" in comm.body

            task = OpsTask.objects.get()
            assert task.status == OpsTask.Status.OPEN
            assert "TEN-0001" in task.title

    def test_daily_rerun_does_not_duplicate(self, org, overdue_tenancy, arrears_rule):
        with org_context(org.pk):
            evaluate_arrears_for_org(org.pk, as_of=self.AS_OF)
            evaluate_arrears_for_org(org.pk, as_of=self.AS_OF + timedelta(days=1))
            assert Communication.objects.count() == 1
            assert OpsTask.objects.count() == 1

    def test_below_threshold_does_not_fire(self, org, overdue_tenancy, arrears_rule):
        with org_context(org.pk):
            fired = evaluate_arrears_for_org(org.pk, as_of=date(2026, 1, 5))
            assert fired == 0
            assert Communication.objects.count() == 0
            # But the case is still opened/tracked.
            case = ArrearsCase.objects.get()
            assert case.state == ArrearsCase.State.OVERDUE

    def test_payment_resolves_case_via_outbox(self, org, overdue_tenancy, arrears_rule):
        with org_context(org.pk):
            evaluate_arrears_for_org(org.pk, as_of=self.AS_OF)
            assert ArrearsCase.objects.exclude(state="resolved").exists()

            # Tenant pays everything owing; the payment path emits the event.
            apply_receipt_to_charges(overdue_tenancy.pk, 195_000)
            emit_event(
                org.pk,
                OutboxEvent.Type.RENT_RECEIVED,
                {"tenancy_id": str(overdue_tenancy.pk), "amount_cents": 195_000},
            )
        handled = process_outbox()
        assert handled == 1
        with org_context(org.pk):
            case = ArrearsCase.objects.get()
            assert case.state == ArrearsCase.State.RESOLVED
            assert case.resolved_at is not None
            event = OutboxEvent.objects.get()
            assert event.processed_at is not None

    def test_new_episode_fires_again(self, org, overdue_tenancy, arrears_rule):
        with org_context(org.pk):
            evaluate_arrears_for_org(org.pk, as_of=self.AS_OF)
            # Pay up fully — case resolves.
            apply_receipt_to_charges(overdue_tenancy.pk, 195_000)
            evaluate_arrears_for_org(org.pk, as_of=self.AS_OF)
            assert not ArrearsCase.objects.exclude(state="resolved").exists()

            # A new charge falls due and goes unpaid: fresh episode, new firing.
            schedule = RentSchedule.objects.get()
            generate_rent_charges(schedule, until=date(2026, 2, 12))
            evaluate_arrears_for_org(org.pk, as_of=date(2026, 2, 20))
            assert ArrearsCase.objects.exclude(state="resolved").count() == 1
            assert Communication.objects.count() == 2


class TestOutbox:
    def test_processing_is_idempotent(self, org, tenancy):
        with org_context(org.pk):
            emit_event(org.pk, OutboxEvent.Type.RENT_RECEIVED, {"tenancy_id": str(tenancy.pk)})
        assert process_outbox() == 1
        assert process_outbox() == 0

    def test_failed_event_records_error_and_batch_continues(self, org, tenancy, arrears_rule, monkeypatch):
        with org_context(org.pk):
            emit_event(org.pk, OutboxEvent.Type.RENT_RECEIVED, {"tenancy_id": str(tenancy.pk)})
            emit_event(org.pk, OutboxEvent.Type.PAYMENT_DISHONOURED, {"tenancy_id": str(tenancy.pk)})

        import automation.services as services

        original = services._handle_event

        def flaky(event):
            if event.event_type == OutboxEvent.Type.RENT_RECEIVED:
                raise RuntimeError("boom")
            return original(event)

        monkeypatch.setattr(services, "_handle_event", flaky)
        handled = process_outbox()
        assert handled == 1  # the dishonour event still processed
        with org_context(org.pk):
            failed = OutboxEvent.objects.get(event_type=OutboxEvent.Type.RENT_RECEIVED)
            assert failed.processed_at is None
            assert failed.attempts == 1
            assert "boom" in failed.last_error


class TestCommunicationApprovalFlow:
    @pytest.fixture
    def draft(self, org, tenancy):
        with org_context(org.pk):
            yield Communication.objects.create(
                organisation=org,
                tenancy=tenancy,
                channel="email",
                recipient="tenant@example.com",
                subject="Test",
                body="Body",
            )

    def test_approve_then_send(self, org, user, draft):
        api = APIClient()
        api.force_authenticate(user)
        response = api.post(f"/api/v1/automation/communications/{draft.pk}/approve/")
        assert response.status_code == 200
        assert response.data["status"] == "approved"
        response = api.post(f"/api/v1/automation/communications/{draft.pk}/send/")
        assert response.status_code == 200
        with org_context(org.pk):
            draft.refresh_from_db()
            assert draft.status == Communication.Status.SENT
            assert draft.approved_by == user
            assert draft.sent_at is not None

    def test_cannot_send_unapproved(self, org, user, draft):
        api = APIClient()
        api.force_authenticate(user)
        response = api.post(f"/api/v1/automation/communications/{draft.pk}/send/")
        assert response.status_code == 400
        with org_context(org.pk):
            draft.refresh_from_db()
            assert draft.status == Communication.Status.PENDING_APPROVAL

    def test_viewer_cannot_approve(self, org, draft):
        viewer = User.objects.create_user("viewer2@harbour.example", "test-pass-123")
        Membership.objects.create(user=viewer, organisation=org, role=Membership.Role.VIEWER)
        api = APIClient()
        api.force_authenticate(viewer)
        response = api.post(f"/api/v1/automation/communications/{draft.pk}/approve/")
        assert response.status_code == 403

    def test_rejected_is_terminal(self, org, user, draft):
        api = APIClient()
        api.force_authenticate(user)
        api.post(f"/api/v1/automation/communications/{draft.pk}/reject/")
        response = api.post(f"/api/v1/automation/communications/{draft.pk}/approve/")
        assert response.status_code == 400


class TestWebhookEmitsEvent:
    def test_rent_webhook_writes_outbox_event(self, client, org, tenancy, trust_account):
        import json

        from payments.models import TenancyPaymentReference
        from payments.providers import SandboxProvider

        with org_context(org.pk):
            ref = TenancyPaymentReference.objects.create(
                organisation=org, tenancy=tenancy, trust_account=trust_account
            )
        payload = {
            "transactionId": "txn-evt-1",
            "reference": ref.reference,
            "amountCents": 65_000,
            "paidAt": "2026-01-08T09:30:00+11:00",
        }
        body = json.dumps(payload).encode()
        response = client.post(
            "/api/v1/payments/webhooks/sandbox/",
            data=body,
            content_type="application/json",
            headers={"X-Webhook-Signature": SandboxProvider().sign(body)},
        )
        assert response.status_code == 200
        with org_context(org.pk):
            event = OutboxEvent.objects.get()
            assert event.event_type == OutboxEvent.Type.RENT_RECEIVED
            assert event.payload["tenancy_id"] == str(tenancy.pk)
            assert event.processed_at is None  # picked up by the worker
