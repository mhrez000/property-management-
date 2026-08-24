import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from common.models import TimeStampedModel


class Organisation(TimeStampedModel):
    """An agency (or self-managing landlord account) — the tenancy boundary.

    Deliberately NOT org-scoped itself: it is the root of the tenant
    hierarchy. Access is controlled through Membership.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    # ABN is optional: self-managing landlords may not have one.
    abn = models.CharField(max_length=11, blank=True)
    timezone = models.CharField(max_length=64, default="Australia/Sydney")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """Email-login user. Global (not org-scoped); org access via Membership."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class Membership(TimeStampedModel):
    """Links a user to an organisation with a role.

    Not RLS-protected: it must be readable before a tenant context exists
    (it is how the context gets resolved). Queries are always keyed by user.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        PROPERTY_MANAGER = "property_manager", "Property Manager"
        FINANCE = "finance", "Finance / Trust Accounting"
        VIEWER = "viewer", "Read-only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.PROPERTY_MANAGER)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organisation"], name="uniq_membership")
        ]

    def __str__(self):
        return f"{self.user} @ {self.organisation} ({self.role})"

    WRITE_ROLES = {Role.ADMIN, Role.PROPERTY_MANAGER, Role.FINANCE}
    MONEY_ROLES = {Role.ADMIN, Role.FINANCE}

    @property
    def can_write(self) -> bool:
        return self.role in self.WRITE_ROLES

    @property
    def can_move_money(self) -> bool:
        return self.role in self.MONEY_ROLES
