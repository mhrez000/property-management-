from django.db import migrations

from common.rls import disable_rls_sql, enable_rls_sql

NEW_ORG_SCOPED_TABLES = [
    "compliance_bondlodgement",
    "ledger_disbursementrun",
    "ledger_disbursementline",
]


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0001_row_level_security"),
        ("compliance", "0003_bondlodgement_bondlodgement_bond_amount_positive"),
        ("ledger", "0003_disbursementrun_disbursementline_and_more"),
    ]

    operations = [
        migrations.RunSQL(enable_rls_sql(table), disable_rls_sql(table))
        for table in NEW_ORG_SCOPED_TABLES
    ]
