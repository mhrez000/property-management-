"""Cross-tenant isolation tests: RLS must block access at the database level.

These tests assert the *failure* mode is "see nothing": no org context ⇒ no
rows; org A's context ⇒ only org A's rows; writing a row stamped with another
organisation ⇒ rejected by Postgres, whatever the application layer does.
"""

import pytest
from django.db import ProgrammingError, connection, transaction

from common.tenancy import bypass_rls, org_context
from portfolio.models import Property

pytestmark = pytest.mark.django_db


def _make_property(org, street):
    return Property.objects.create(
        organisation=org, address_line_1=street, suburb="Testville", state="NSW", postcode="2000"
    )


def test_no_context_sees_nothing(org):
    with org_context(org.pk):
        _make_property(org, "1 Hidden St")
    assert Property.objects.count() == 0


def test_context_scopes_to_own_org(org, other_org):
    with org_context(org.pk):
        _make_property(org, "1 Mine St")
    with org_context(other_org.pk):
        _make_property(other_org, "1 Theirs St")

    with org_context(org.pk):
        addresses = list(Property.objects.values_list("address_line_1", flat=True))
        assert addresses == ["1 Mine St"]

    with org_context(other_org.pk):
        addresses = list(Property.objects.values_list("address_line_1", flat=True))
        assert addresses == ["1 Theirs St"]


def test_cannot_insert_row_for_another_org(org, other_org):
    with org_context(org.pk):
        with pytest.raises(ProgrammingError, match="row-level security"):
            with transaction.atomic():
                _make_property(other_org, "1 Forged St")


def test_bypass_is_explicit_and_scoped(org, other_org):
    with org_context(org.pk):
        _make_property(org, "1 Mine St")
    with org_context(other_org.pk):
        _make_property(other_org, "1 Theirs St")

    with bypass_rls():
        assert Property.objects.count() == 2
    assert Property.objects.count() == 0


def test_rls_is_forced_for_table_owner():
    """The app role owns its tables; FORCE RLS must be on or policies are moot."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relforcerowsecurity, relrowsecurity FROM pg_class WHERE relname = %s",
            ["portfolio_property"],
        )
        force, enabled = cursor.fetchone()
    assert enabled is True
    assert force is True


def test_app_role_is_not_superuser():
    """Superusers bypass RLS entirely — the app must never connect as one."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT usesuper FROM pg_user WHERE usename = current_user")
        (is_super,) = cursor.fetchone()
    assert is_super is False
