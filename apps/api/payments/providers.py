"""Payment provider abstraction.

Live candidates (all PayTo-capable, per the blueprint): Monoova (first
choice — rental-collections product, AFSL 421414), Zepto, Azupay, Ezidebit.
Each concrete provider implements webhook verification + payload parsing;
the matching engine downstream is provider-agnostic.

Only a sandbox provider ships at this stage: live integration is gated on
provider onboarding and Australian financial-services legal advice (AFSL
structuring) per the roadmap.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.utils.dateparse import parse_datetime


class WebhookVerificationError(Exception):
    pass


@dataclass(frozen=True)
class PaymentNotification:
    provider: str
    provider_txn_id: str
    reference: str
    amount_cents: int
    paid_at: datetime
    raw: dict


class PaymentProvider:
    """Interface every payment provider adapter implements."""

    name: str

    def verify_and_parse(self, body: bytes, headers) -> PaymentNotification:
        raise NotImplementedError

    def sign(self, body: bytes) -> str:
        """Compute the webhook signature for a payload (used by tests/sandbox)."""
        raise NotImplementedError


class SandboxProvider(PaymentProvider):
    """HMAC-SHA256-signed JSON webhooks, mirroring the Monoova/Zepto shape:

    ``{"transactionId": ..., "reference": ..., "amountCents": ..., "paidAt": ...}``
    with the hex signature in an ``X-Webhook-Signature`` header.
    """

    name = "sandbox"

    def _secret(self) -> bytes:
        return settings.PAYMENTS_WEBHOOK_SECRET.encode()

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret(), body, hashlib.sha256).hexdigest()

    def verify_and_parse(self, body: bytes, headers) -> PaymentNotification:
        provided = headers.get("X-Webhook-Signature", "")
        if not hmac.compare_digest(self.sign(body), provided):
            raise WebhookVerificationError("Invalid webhook signature")
        try:
            payload = json.loads(body)
            paid_at = parse_datetime(payload["paidAt"])
            if paid_at is None:
                raise ValueError("paidAt is not a valid datetime")
            amount = payload["amountCents"]
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                raise ValueError("amountCents must be a positive integer")
            return PaymentNotification(
                provider=self.name,
                provider_txn_id=str(payload["transactionId"]),
                reference=str(payload["reference"]),
                amount_cents=amount,
                paid_at=paid_at,
                raw=payload,
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise WebhookVerificationError(f"Malformed webhook payload: {exc}") from exc


_PROVIDERS: dict[str, PaymentProvider] = {SandboxProvider.name: SandboxProvider()}


def get_provider(name: str) -> PaymentProvider | None:
    return _PROVIDERS.get(name)
