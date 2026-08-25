from django.db import migrations

from common.rls import disable_rls_sql, enable_rls_sql

AUTOMATION_TABLES = [
    "automation_outboxevent",
    "automation_automationrule",
    "automation_rulefiring",
    "automation_opstask",
    "automation_communicationtemplate",
    "automation_communication",
    "automation_arrearscase",
]


class Migration(migrations.Migration):
    dependencies = [
        ("automation", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(enable_rls_sql(table), disable_rls_sql(table))
        for table in AUTOMATION_TABLES
    ]
