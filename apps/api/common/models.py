import uuid

from django.db import models

from common.tenancy import get_current_org_id


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OrgScopedModel(TimeStampedModel):
    """Base for every tenant-scoped table.

    Carries the mandatory ``organisation_id`` and defaults it from the active
    tenant context so application code cannot forget to set it. Row-Level
    Security policies (see ``common.rls``) enforce isolation at the database
    level regardless of what the application does.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organisation = models.ForeignKey(
        "accounts.Organisation",
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.organisation_id is None:
            org_id = get_current_org_id()
            if org_id is not None:
                self.organisation_id = org_id
        super().save(*args, **kwargs)
