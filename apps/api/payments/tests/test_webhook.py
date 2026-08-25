"""End-to-end sandbox payment path: webhook → trust receipt → rent charges.

This is the Phase-0 "money-in" prototype threshold from the blueprint:
a sandbox rent payment must reconcile automatically and idempotently.
"""

import json
from datetime import date

import pytest
from django.urls import reverse

from common.tenancy import org_context
from ledger.models import JournalEntry, Receipt
from payments.models import IncomingPayment, TenancyPaymentReference
from payments.providers import SandboxProvider
from portfolio.models import Lease, RentCharge, RentSchedule
from portfolio.services import generate_rent_charges

pytestmark = pytest.mark.django_db


@pytest.fixture
def payment_ref(org, tenancy, trust_account):
    with org_context(org.pk):
        yield TenancyPaymentReference.objects.create(
            organisation=org, tenancy=tenancy, trust_account=trust_account, provider="sandbox"
        )


def _post_webhook(client, payload: dict, *, sign=True, tamper=False):
    body = json.dumps(payload).encode()
    signature = SandboxProvider().sign(body) if sign else "bad-signature"
    if tamper:
        body = body + b" "
    return client.post(
        reverse("payment-webhook", kwargs={"version": "v1", "provider_name": "sandbox"}),
        data=body,
        content_type="application/json",
        headers={"X-Webhook-Signature": signature},
    )


def _payload(reference, txn_id="txn-100", amount=65_000):
    return {
        "transactionId": txn_id,
        "reference": reference,
        "amountCents": amount,
        "paidAt": "2026-01-08T09:30:00+11:00",
    }


class TestWebhookSecurity:
    def test_rejects_bad_signature(self, client, payment_ref):
        response = _post_webhook(client, _payload(payment_ref.reference), sign=False)
        assert response.status_code == 400
        assert JournalEntry.objects.count() == 0

    def test_rejects_tampered_body(self, client, payment_ref):
        response = _post_webhook(client, _payload(payment_ref.reference), tamper=True)
        assert response.status_code == 400

    def test_unknown_provider_404(self, client):
        response = client.post(
            reverse("payment-webhook", kwargs={"version": "v1", "provider_name": "nope"}),
            data=b"{}",
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_unknown_reference_404_for_retry(self, client, payment_ref):
        response = _post_webhook(client, _payload("PC-DOESNOTEXIST"))
        assert response.status_code == 404


class TestWebhookReceipting:
    def test_payment_creates_receipt_and_settles_rent(self, client, org, tenancy, payment_ref):
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
            generate_rent_charges(schedule, until=date(2026, 1, 7))

        response = _post_webhook(client, _payload(payment_ref.reference))
        assert response.status_code == 200

        with org_context(org.pk):
            payment = IncomingPayment.objects.get()
            assert payment.status == IncomingPayment.Status.MATCHED
            receipt = Receipt.objects.get()
            assert receipt.amount_cents == 65_000
            assert receipt.number == 1
            assert payment.receipt_id == receipt.pk
            charge = RentCharge.objects.get()
            assert charge.status == RentCharge.Status.PAID

    def test_retried_webhook_is_idempotent(self, client, org, payment_ref):
        first = _post_webhook(client, _payload(payment_ref.reference))
        retry = _post_webhook(client, _payload(payment_ref.reference))
        assert first.status_code == 200
        assert retry.status_code == 200
        with org_context(org.pk):
            assert IncomingPayment.objects.count() == 1
            assert Receipt.objects.count() == 1
            assert JournalEntry.objects.count() == 1

    def test_distinct_transactions_both_receipt(self, client, org, payment_ref):
        _post_webhook(client, _payload(payment_ref.reference, txn_id="txn-1"))
        _post_webhook(client, _payload(payment_ref.reference, txn_id="txn-2"))
        with org_context(org.pk):
            assert Receipt.objects.count() == 2
            assert [r.number for r in Receipt.objects.order_by("number")] == [1, 2]
