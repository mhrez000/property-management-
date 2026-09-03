"""The relational spine: properties, owners, tenants, tenancies, leases, rent.

All models are org-scoped (RLS). Money is integer cents. Lifecycle fields are
explicit state machines with allowed-transition maps; transitions should go
through the model methods so invalid jumps raise instead of silently saving.
"""

from django.core.exceptions import ValidationError
from django.db import models

from common.constants import Jurisdiction
from common.models import OrgScopedModel


class Owner(OrgScopedModel):
    """A property owner (landlord client of the agency)."""

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    # Owner disbursement destination (label only at MVP; provider tokens later).
    bank_account_label = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organisation", "name"])]

    def __str__(self):
        return self.name


class Property(OrgScopedModel):
    """A managed rental property."""

    address_line_1 = models.CharField(max_length=255)
    address_line_2 = models.CharField(max_length=255, blank=True)
    suburb = models.CharField(max_length=128)
    state = models.CharField(max_length=3, choices=Jurisdiction.choices)
    postcode = models.CharField(max_length=4)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    car_spaces = models.PositiveSmallIntegerField(null=True, blank=True)
    owners = models.ManyToManyField(Owner, through="OwnerProperty", related_name="properties")
    assigned_manager = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_properties",
    )
    is_active = models.BooleanField(default=True)
    management_fee_bps = models.PositiveIntegerField(
        default=0,
        help_text="Agency management fee in basis points of rent collected (e.g. 770 = 7.7%).",
    )

    class Meta:
        verbose_name_plural = "properties"
        indexes = [
            models.Index(fields=["organisation", "state"]),
            models.Index(fields=["organisation", "suburb"]),
        ]

    def __str__(self):
        return f"{self.address_line_1}, {self.suburb} {self.state} {self.postcode}"


class OwnerProperty(OrgScopedModel):
    """Owner↔property with ownership share, for split disbursements.

    Shares are basis points (10000 = 100%) so splits stay in integer math.
    """

    owner = models.ForeignKey(Owner, on_delete=models.CASCADE, related_name="ownerships")
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="ownerships")
    share_basis_points = models.PositiveIntegerField(default=10000)

    class Meta:
        verbose_name_plural = "owner properties"
        constraints = [
            models.UniqueConstraint(fields=["owner", "property"], name="uniq_owner_property"),
            models.CheckConstraint(
                check=models.Q(share_basis_points__gt=0, share_basis_points__lte=10000),
                name="ownership_share_valid",
            ),
        ]


class Tenant(OrgScopedModel):
    """A renter (person). One person may appear on multiple tenancies."""

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organisation", "name"])]

    def __str__(self):
        return self.name


class Tenancy(OrgScopedModel):
    """An occupancy of a property by one or more tenants.

    The tenancy (not the individual tenant) is the unit rent is charged to,
    the sub-ledger is kept for, and the payment reference is issued against.
    """

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="tenancies")
    tenants = models.ManyToManyField(Tenant, related_name="tenancies")
    reference = models.CharField(
        max_length=32, help_text="Human-readable tenancy reference, unique within the organisation."
    )

    class Meta:
        verbose_name_plural = "tenancies"
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "reference"], name="uniq_tenancy_reference"
            )
        ]

    def __str__(self):
        return self.reference


class Lease(OrgScopedModel):
    """A lease agreement over a tenancy, with an explicit lifecycle."""

    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ENDING = "ending", "Ending"
        ENDED = "ended", "Ended"

    ALLOWED_TRANSITIONS = {
        State.DRAFT: {State.ACTIVE},
        State.ACTIVE: {State.ENDING, State.ENDED},
        State.ENDING: {State.ENDED},
        State.ENDED: set(),
    }

    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="leases")
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Fixed-term end; blank = periodic.")
    rent_amount_cents = models.BigIntegerField()
    rent_frequency = models.CharField(
        max_length=16,
        choices=[("weekly", "Weekly"), ("fortnightly", "Fortnightly"), ("monthly", "Monthly")],
        default="weekly",
    )
    bond_amount_cents = models.BigIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["organisation", "state"])]
        constraints = [
            models.CheckConstraint(check=models.Q(rent_amount_cents__gt=0), name="lease_rent_positive"),
            models.CheckConstraint(check=models.Q(bond_amount_cents__gte=0), name="lease_bond_non_negative"),
        ]

    def transition_to(self, new_state: str):
        allowed = self.ALLOWED_TRANSITIONS[self.State(self.state)]
        if self.State(new_state) not in allowed:
            raise ValidationError(
                f"Invalid lease transition {self.state} → {new_state}; allowed: {sorted(allowed)}"
            )
        self.state = new_state
        self.save(update_fields=["state", "updated_at"])


class RentSchedule(OrgScopedModel):
    """Generates the rent obligations for a lease.

    A lease has one active schedule at a time; a rent increase closes the old
    schedule (sets ``end_date``) and opens a new one — history is preserved.
    """

    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        FORTNIGHTLY = "fortnightly", "Fortnightly"
        MONTHLY = "monthly", "Monthly"

    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name="rent_schedules")
    amount_cents = models.BigIntegerField()
    frequency = models.CharField(max_length=16, choices=Frequency.choices)
    first_due_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(amount_cents__gt=0), name="schedule_amount_positive")
        ]

    def __str__(self):
        return f"{self.lease_id} {self.get_frequency_display()} ${self.amount_cents / 100:.2f}"


class RentCharge(OrgScopedModel):
    """A single rent obligation (one period) generated from a schedule.

    Receipts posted against the tenancy sub-ledger settle charges
    oldest-first; ``status`` is derived bookkeeping for arrears views.
    """

    class Status(models.TextChoices):
        DUE = "due", "Due"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"

    schedule = models.ForeignKey(RentSchedule, on_delete=models.PROTECT, related_name="charges")
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="rent_charges")
    due_date = models.DateField()
    period_start = models.DateField()
    period_end = models.DateField()
    amount_cents = models.BigIntegerField()
    paid_cents = models.BigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DUE)

    class Meta:
        indexes = [
            models.Index(fields=["organisation", "tenancy", "due_date"]),
            models.Index(
                fields=["organisation", "due_date"],
                condition=~models.Q(status="paid"),
                name="rentcharge_unpaid_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "due_date"], name="uniq_charge_per_schedule_due_date"
            ),
            models.CheckConstraint(check=models.Q(amount_cents__gt=0), name="charge_amount_positive"),
            models.CheckConstraint(
                check=models.Q(paid_cents__gte=0) & models.Q(paid_cents__lte=models.F("amount_cents")),
                name="charge_paid_within_amount",
            ),
        ]
        ordering = ["due_date"]


# Module-level alias so drf-spectacular ENUM_NAME_OVERRIDES can resolve the
# nested TextChoices by import string.
LEASE_STATE_CHOICES = Lease.State.choices
