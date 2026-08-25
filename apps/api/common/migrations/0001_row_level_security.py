"""Row-Level Security on every tenant-scoped table.

Isolation is enforced by Postgres, not application filters: with no
``app.current_org`` context a query sees zero rows, and a mismatched
``organisation_id`` on INSERT/UPDATE is rejected. ``FORCE`` makes the
policies bind the table owner too, since the app role owns its tables.

Auto-created M2M join tables (e.g. ``portfolio_tenancy_tenants``) carry no
``organisation_id`` and are not RLS-protected; their rows are only id pairs
and are always reached by joining through protected tables. Revisit if a
join table ever gains sensitive payload columns.
"""

from django.db import migrations

from common.rls import disable_rls_sql, enable_rls_sql

ORG_SCOPED_TABLES = [
    "portfolio_owner",
    "portfolio_property",
    "portfolio_ownerproperty",
    "portfolio_tenant",
    "portfolio_tenancy",
    "portfolio_lease",
    "portfolio_rentschedule",
    "portfolio_rentcharge",
    "ledger_trustaccount",
    "ledger_account",
    "ledger_journalentry",
    "ledger_posting",
    "ledger_receiptsequence",
    "ledger_receipt",
    "ledger_banktransaction",
    "ledger_reconciliation",
    "payments_tenancypaymentreference",
    "payments_incomingpayment",
]


class Migration(migrations.Migration):
    dependencies = [
        ("portfolio", "0001_initial"),
        ("ledger", "0001_initial"),
        ("payments", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(enable_rls_sql(table), disable_rls_sql(table))
        for table in ORG_SCOPED_TABLES
    ]
