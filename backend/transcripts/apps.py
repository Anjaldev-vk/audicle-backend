from django.apps import AppConfig


class TranscriptsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "transcripts"

    def ready(self):
        pass
