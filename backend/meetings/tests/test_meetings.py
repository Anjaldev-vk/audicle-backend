import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from meetings.models import Meeting


@pytest.fixture
def zoom_meeting(db, org_admin, organisation):
    return Meeting.objects.create(
        title="Zoom Standup",
        platform=Meeting.Platform.ZOOM,
        meeting_url="https://zoom.us/j/123456",
        created_by=org_admin,
        organisation=organisation,
    )


@pytest.fixture
def individual_meeting(db, individual_user):
    return Meeting.objects.create(
        title="Personal Sync",
        platform=Meeting.Platform.UPLOAD,
        created_by=individual_user,
        organisation=None,
    )


# ── Create meeting ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCreateMeeting:

    def test_individual_can_create_upload_meeting(self, auth_client):
        # auth_client uses individual_user
        response = auth_client.post(
            reverse("meetings:meeting-list-create"),
            {
                "title":    "My Meeting",
                "platform": "upload",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["data"]["title"] == "My Meeting"

    def test_org_user_can_create_zoom_meeting(self, org_admin_client):
        response = org_admin_client.post(
            reverse("meetings:meeting-list-create"),
            {
                "title":       "Org Meeting",
                "platform":    "zoom",
                "meeting_url": "https://zoom.us/j/111222",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["data"]["title"] == "Org Meeting"

    def test_meeting_scoped_to_org(self, org_admin_client, organisation):
        org_admin_client.post(
            reverse("meetings:meeting-list-create"),
            {
                "title":       "Org Meeting",
                "platform":    "zoom",
                "meeting_url": "https://zoom.us/j/111222",
            },
            format="json",
        )
        meeting = Meeting.objects.get(title="Org Meeting")
        assert meeting.organisation == organisation

    def test_individual_meeting_has_no_org(self, auth_client):
        auth_client.post(
            reverse("meetings:meeting-list-create"),
            {"title": "Solo", "platform": "upload"},
            format="json",
        )
        meeting = Meeting.objects.get(title="Solo")
        assert meeting.organisation is None


# ── List meetings ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestListMeetings:

    def test_org_member_sees_org_meetings(self, org_member_client, zoom_meeting):
        response = org_member_client.get(reverse("meetings:meeting-list-create"))
        assert response.status_code == 200
        results = response.json()["data"].get("results", response.json()["data"])
        ids = [m["id"] for m in results]
        assert str(zoom_meeting.id) in ids

    def test_individual_sees_only_own_meetings(
        self, auth_client, individual_meeting, zoom_meeting
    ):
        response = auth_client.get(reverse("meetings:meeting-list-create"))
        results = response.json()["data"].get("results", response.json()["data"])
        ids = [m["id"] for m in results]
        assert str(individual_meeting.id) in ids
        assert str(zoom_meeting.id) not in ids

    def test_archived_meetings_excluded(self, org_admin_client, zoom_meeting):
        zoom_meeting.is_archived = True
        zoom_meeting.save()
        response = org_admin_client.get(reverse("meetings:meeting-list-create"))
        results = response.json()["data"].get("results", response.json()["data"])
        ids = [m["id"] for m in results]
        assert str(zoom_meeting.id) not in ids


# ── Get meeting detail ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMeetingDetail:

    def test_owner_can_get_meeting(self, org_admin_client, zoom_meeting):
        response = org_admin_client.get(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id])
        )
        assert response.status_code == 200
        assert response.json()["data"]["id"] == str(zoom_meeting.id)

    def test_org_member_can_get_meeting(self, org_member_client, zoom_meeting):
        response = org_member_client.get(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id])
        )
        assert response.status_code == 200


# ── Update meeting ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUpdateMeeting:

    def test_owner_can_update_title(self, org_admin_client, zoom_meeting):
        response = org_admin_client.patch(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id]),
            {"title": "Updated Title"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["data"]["title"] == "Updated Title"

    def test_member_cannot_update_meeting(self, org_member_client, zoom_meeting):
        response = org_member_client.patch(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id]),
            {"title": "Hacked"},
            format="json",
        )
        assert response.status_code == 403

    def test_cannot_update_non_scheduled_meeting(
        self, org_admin_client, zoom_meeting
    ):
        zoom_meeting.status = Meeting.Status.RECORDING
        zoom_meeting.save()
        response = org_admin_client.patch(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id]),
            {"title": "New Title"},
            format="json",
        )
        assert response.status_code == 400


# ── Delete meeting ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDeleteMeeting:

    def test_owner_can_archive_meeting(self, org_admin_client, zoom_meeting):
        response = org_admin_client.delete(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id])
        )
        assert response.status_code == 200
        zoom_meeting.refresh_from_db()
        assert zoom_meeting.is_archived is True

    def test_member_cannot_delete_meeting(self, org_member_client, zoom_meeting):
        response = org_member_client.delete(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id])
        )
        assert response.status_code == 403


# ── Duration auto-compute ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDurationAutoCompute:

    def test_duration_computed_on_save(self, zoom_meeting):
        from django.utils import timezone
        from datetime import timedelta

        zoom_meeting.started_at = timezone.now()
        zoom_meeting.ended_at   = zoom_meeting.started_at + timedelta(seconds=3600)
        zoom_meeting.save()

        zoom_meeting.refresh_from_db()
        assert zoom_meeting.duration_seconds == 3600

    def test_duration_null_without_timestamps(self, zoom_meeting):
        zoom_meeting.refresh_from_db()
        assert zoom_meeting.duration_seconds is None
