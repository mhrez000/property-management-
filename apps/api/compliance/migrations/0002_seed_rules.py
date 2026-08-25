from django.db import migrations

from compliance.seed import load_seed_rules


def forward(apps, schema_editor):
    load_seed_rules(apps.get_model("compliance", "ComplianceRule"))


def backward(apps, schema_editor):
    apps.get_model("compliance", "ComplianceRule").objects.filter(
        source="Founding research documents (secondary sources)"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("compliance", "0001_initial"),
    ]

    operations = [migrations.RunPython(forward, backward)]
