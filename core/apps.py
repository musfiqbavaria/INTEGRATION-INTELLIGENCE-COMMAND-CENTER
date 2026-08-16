from django.apps import AppConfig
class CoreConfig(AppConfig):
    default_auto_field="django.db.models.BigAutoField"
    name="core"
    def ready(self):
        # Importing registers the workflow event triggers.
        from . import signals  # noqa: F401
