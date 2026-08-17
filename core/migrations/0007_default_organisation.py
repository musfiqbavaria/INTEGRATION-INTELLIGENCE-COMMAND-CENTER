"""Create the founding organisation and attach every existing record to it.

`organisation` is nullable so the column could be added to a live table without
a rewrite, but a permanently null tenant key is a record that belongs to nobody
and would disappear the moment a second entity turned strict scoping on. This
backfill is what makes the single-entity install a proper one-entity install
rather than an unassigned one.

Reversible: dropping back to 0006 detaches the records and removes the row.
"""
from django.db import migrations

# Every model that carries the tenant key.
OWNED = ["AeoEntry", "AiDecision", "AttentionItem", "Automation", "Campaign", "ContentItem",
         "Conversion", "EmailCampaign", "FinancialRecord", "Integration", "Lead", "Setting",
         "SupportTicket", "WhatsAppTemplate"]

DEFAULT = {"name": "Emerald Rozalia Limited", "code": "ER", "country": "IE",
           "base_currency": "EUR", "locale": "en-IE", "timezone": "Europe/Dublin"}


def create_default(apps, schema_editor):
    Organisation = apps.get_model("core", "Organisation")
    organisation, _ = Organisation.objects.get_or_create(code=DEFAULT["code"], defaults=DEFAULT)
    for name in OWNED:
        apps.get_model("core", name).objects.filter(organisation=None).update(organisation=organisation)


def undo(apps, schema_editor):
    Organisation = apps.get_model("core", "Organisation")
    for name in OWNED:
        apps.get_model("core", name).objects.update(organisation=None)
    Organisation.objects.filter(code=DEFAULT["code"]).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0006_conversion_engagementevent_fxrate_organisation_and_more")]

    operations = [migrations.RunPython(create_default, undo)]
