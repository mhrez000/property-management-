"""Tenant (organisation) context management.

Multi-tenancy model: shared schema, mandatory ``organisation_id`` on every
tenant-scoped row, enforced by PostgreSQL Row-Level Security. The active
organisation is communicated to Postgres via the ``app.current_org`` GUC;
RLS policies compare it against each row's ``organisation_id``.

The failure mode is deliberately "see nothing": with no organisation context
set, every RLS-protected query returns zero rows. Background jobs and
operational tooling that legitimately need cross-tenant access opt in
explicitly via :func:`bypass_rls`.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connection

_current_org: ContextVar[uuid.UUID | None] = ContextVar("current_org", default=None)


def get_current_org_id() -> uuid.UUID | None:
    return _current_org.get()


def _set_guc(name: str, value: str) -> None:
    with connection.cursor() as cursor:
        # set_config with is_local=false: survives for the session but is
        # still rolled back if the enclosing transaction aborts.
        cursor.execute("SELECT set_config(%s, %s, false)", [name, value])


def activate_org(org_id: uuid.UUID | str) -> None:
    org_id = uuid.UUID(str(org_id))
    _current_org.set(org_id)
    _set_guc("app.current_org", str(org_id))


def deactivate_org() -> None:
    _current_org.set(None)
    if not connection.in_atomic_block or connection.is_usable():
        try:
            _set_guc("app.current_org", "")
            _set_guc("app.bypass_rls", "")
        except Exception:
            # Connection already closed/broken — nothing to reset.
            pass


@contextmanager
def org_context(org_id: uuid.UUID | str):
    """Run a block with the given organisation active (jobs, tests, shells)."""
    previous = _current_org.get()
    activate_org(org_id)
    try:
        yield
    finally:
        if previous is None:
            deactivate_org()
        else:
            activate_org(previous)


@contextmanager
def bypass_rls():
    """Run a block with RLS bypassed (cross-tenant admin/system work only).

    Use sparingly and never in request handlers serving tenant data.
    """
    _set_guc("app.bypass_rls", "on")
    try:
        yield
    finally:
        _set_guc("app.bypass_rls", "")
