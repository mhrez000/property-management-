"""Payments: NPP rent collection behind a provider-agnostic interface.

Product rule: tenants are never charged a fee to pay rent. Provider
transaction fees are absorbed into agency plan pricing.

The pattern (per the blueprint): each tenancy gets a unique payment
reference (virtual PayID/CRN with a real provider); the provider webhooks us
on every payment; the matching engine posts an idempotent trust receipt keyed
on the provider transaction id.
"""

import secrets

from django.db import models

from common.models import OrgScopedModel


def generate_payment_reference() -> str:
    """Globally-unique human-readable reference, e.g. ``PC-83F2K9Q4TN``.

    Globally unique (not per-org) because inbound webhooks are resolved by
    reference alone, before any tenant context exists.
    """
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/I
    return "PC-" + "".join(secrets.choice(alphabet) for _ in range(10))


class TenancyPaymentReference(OrgScopedModel):
    """The unique rent-payment identity for a tenancy with a provider."""

    tenancy = models.OneToOneField(
        "portfolio.Tenancy", on_delete=models.PROTECT, related_name="payment_reference"
    )
    trust_account = models.ForeignKey(
        "ledger.TrustAccount", on_delete=models.PROTECT, related_name="payment_references"
    )
    provider = models.CharField(max_length=32, default="sandbox")
    reference = models.CharField(max_length=32, unique=True, default=generate_payment_reference)
    payid = models.CharField(
        max_length=255, blank=True, help_text="Provider-issued virtual PayID, once registered."
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.reference} → {self.tenancy_id}"


class IncomingPayment(OrgScopedModel):
    """An inbound payment notification from a provider webhook.

    Kept verbatim (``raw``) for audit; ``provider_txn_id`` is the idempotency
    key end-to-end — it becomes the journal entry's ``external_id``.
    """

    class Status(models.TextChoices):
        MATCHED = "matched", "Matched and receipted"
        UNMATCHED = "unmatched", "No matching tenancy reference"

    provider = models.CharField(max_length=32)
    provider_txn_id = models.CharField(max_length=255)
    reference = models.CharField(max_length=64)
    amount_cents = models.BigIntegerField()
    paid_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices)
    raw = models.JSONField(default=dict)
    receipt = models.ForeignKey(
        "ledger.Receipt", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_txn_id"], name="uniq_incoming_payment_txn"
            )
        ]
        indexes = [models.Index(fields=["organisation", "status"])]
