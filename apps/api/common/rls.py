"""SQL builders for Row-Level Security migrations.

Every tenant-scoped table gets:

- ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` — FORCE is essential because the
  application role owns the tables it migrates, and owners are otherwise
  exempt from RLS.
- an isolation policy matching ``organisation_id`` against the
  ``app.current_org`` GUC (NULL/unset context ⇒ no rows visible), and
- a bypass policy gated on ``app.bypass_rls`` for explicit system-level work.

The application must connect as a non-superuser role: superusers skip RLS
entirely.
"""

ORG_MATCH = "organisation_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
BYPASS = "COALESCE(current_setting('app.bypass_rls', true), '') = 'on'"


def enable_rls_sql(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY {table}_org_isolation ON {table}
    USING ({ORG_MATCH})
    WITH CHECK ({ORG_MATCH});
CREATE POLICY {table}_rls_bypass ON {table}
    USING ({BYPASS})
    WITH CHECK ({BYPASS});
"""


def disable_rls_sql(table: str) -> str:
    return f"""
DROP POLICY IF EXISTS {table}_org_isolation ON {table};
DROP POLICY IF EXISTS {table}_rls_bypass ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""
