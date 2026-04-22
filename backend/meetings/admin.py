from django.contrib import admin

from meetings.models import Meeting, MeetingParticipant


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display   = (
        "title", "platform", "status",
        "created_by", "organisation",
        "scheduled_at", "is_archived",
    )
    list_filter = ("platform", "status", "is_archived")
    search_fields  = ("title", "created_by__email", "organisation__name")
    readonly_fields = (
        "id", "duration_seconds",
        "created_at", "updated_at",
    )
    ordering = ("-created_at",)


@admin.register(MeetingParticipant)
class MeetingParticipantAdmin(admin.ModelAdmin):
    list_display  = ("name", "email", "role", "meeting")
    list_filter   = ("role",)
    search_fields = ("name", "email", "meeting__title")
    readonly_fields = ("id", "created_at", "updated_at")
