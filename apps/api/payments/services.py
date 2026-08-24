"""The matching engine: webhook notification → trust receipt → rent charges."""

from __future__ import annotations

import logging

from django.db import transaction

from common.tenancy import bypass_rls, org_context
from ledger.services import receipt_rent
from payments.models import IncomingPayment, TenancyPaymentReference
from payments.providers import PaymentNotification
from portfolio.services import apply_receipt_to_charges

logger = logging.getLogger(__name__)


def process_notification(notification: PaymentNotification) -> IncomingPayment:
    """Handle one provider payment notification, idempotently.

    A webhook arrives with no tenant context, so the payment reference is
    resolved under an explicit RLS bypass; everything after that runs inside
    the resolved organisation's context. A retried webhook (same provider
    transaction id) returns the already-recorded payment untouched.
    """
    with bypass_rls():
        existing = IncomingPayment.objects.filter(
            provider=notification.provider, provider_txn_id=notification.provider_txn_id
        ).first()
        if existing is not None:
            return existing
        ref = (
            TenancyPaymentReference.objects.select_related("tenancy", "trust_account")
            .filter(reference=notification.reference, is_active=True)
            .first()
        )

    if ref is None:
        logger.warning(
            "Unmatched payment %s/%s ref=%s",
            notification.provider,
            notification.provider_txn_id,
            notification.reference,
        )
        # Every IncomingPayment row is org-scoped, and an unknown reference
        # has no organisation to attribute it to — refuse rather than lose
        # money data, so the provider retries once the reference is fixed.
        raise LookupError(f"No active tenancy payment reference '{notification.reference}'")

    with org_context(ref.organisation_id):
        with transaction.atomic():
            receipt = receipt_rent(
                tenancy=ref.tenancy,
                trust_account=ref.trust_account,
                amount_cents=notification.amount_cents,
                received_from=f"NPP payment ref {notification.reference}",
                external_id=f"{notification.provider}:{notification.provider_txn_id}",
                method="npp",
                posted_at=notification.paid_at,
            )
            payment, created = IncomingPayment.objects.get_or_create(
                provider=notification.provider,
                provider_txn_id=notification.provider_txn_id,
                defaults={
                    "organisation_id": ref.organisation_id,
                    "reference": notification.reference,
                    "amount_cents": notification.amount_cents,
                    "paid_at": notification.paid_at,
                    "status": IncomingPayment.Status.MATCHED,
                    "raw": notification.raw,
                    "receipt": receipt,
                },
            )
            if created:
                apply_receipt_to_charges(ref.tenancy_id, notification.amount_cents)
            return payment
