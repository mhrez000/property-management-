"""Bond lodgement workflow state (org-scoped, RLS-protected).

Unlike the global rules tables in ``compliance.models``, bonds are tenant
data. The lifecycle is jurisdiction-aware: NT bonds settle into
``HELD_IN_TRUST`` (no central authority) while every other state lodges with
its authority by a statutory deadline.
"""

from django.core.exceptions import ValidationError
from django.db import models

from common.models import OrgScopedModel


class BondLodgement(OrgScopedModel):
    class State(models.TextChoices):
        PENDING_RECEIPT = "pending_receipt", "Pending receipt"
        RECEIVED = "received", "Received into trust"
        HELD_IN_TRUST = "held_in_trust", "Held in agent trust (NT)"
        LODGED = "lodged", "Lodged with authority"
        REFUNDED = "refunded", "Refunded"

    ALLOWED_TRANSITIONS = {
        State.PENDING_RECEIPT: {State.RECEIVED},
        State.RECEIVED: {State.HELD_IN_TRUST, State.LODGED},
        State.HELD_IN_TRUST: {State.REFUNDED},
        State.LODGED: {State.REFUNDED},
        State.REFUNDED: set(),
    }

    lease = models.OneToOneField(
        "portfolio.Lease", on_delete=models.PROTECT, related_name="bond_lodgement"
    )
    amount_cents = models.BigIntegerField()
    jurisdiction = models.CharField(max_length=3)
    authority_name = models.CharField(max_length=255)
    held_in_agent_trust = models.BooleanField(default=False)
    state = models.CharField(max_length=32, choices=State.choices, default=State.PENDING_RECEIPT)

    trust_account = models.ForeignKey(
        "ledger.TrustAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    received_on = models.DateField(null=True, blank=True)
    due_date = models.DateField(
        null=True, blank=True, help_text="Statutory lodgement deadline; blank where none applies (NT)."
    )
    authority_reference = models.CharField(max_length=128, blank=True)
    lodged_at = models.DateTimeField(null=True, blank=True)
    receipt_entry = models.ForeignKey(
        "ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    lodgement_entry = models.ForeignKey(
        "ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(amount_cents__gt=0), name="bond_amount_positive")
        ]
        indexes = [models.Index(fields=["organisation", "state"])]

    def transition_to(self, new_state: str):
        allowed = self.ALLOWED_TRANSITIONS[self.State(self.state)]
        if self.State(new_state) not in allowed:
            raise ValidationError(
                f"Invalid bond transition {self.state} → {new_state}; allowed: {sorted(allowed)}"
            )
        self.state = new_state

    def __str__(self):
        return f"Bond {self.lease_id} ({self.jurisdiction}, {self.state})"
