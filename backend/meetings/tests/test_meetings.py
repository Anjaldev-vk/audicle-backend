import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from meetings.models import Meeting


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def individual_user(db):
    from accounts.models import User
    return User.objects.create_user(
        email="individual@example.com",
        password="StrongPass123!",
        first_name="Individual",
        last_name="User",
    )


@pytest.fixture
def org_owner(db):
    from accounts.models import Organisation, User
    org = Organisation.objects.create(name="Test Org", slug="test-org")
    user = User.objects.create_user(
        email="owner@example.com",
        password="StrongPass123!",
        first_name="Org",
        last_name="Owner",
        organisation=org,
        org_role="owner",
    )
    return user


@pytest.fixture
def org_member(db, org_owner):
    from accounts.models import User
    return User.objects.create_user(
        email="member@example.com",
        password="StrongPass123!",
        first_name="Org",
        last_name="Member",
        organisation=org_owner.organisation,
        org_role="member",
    )


@pytest.fixture
def other_org_user(db):
    from accounts.models import Organisation, User
    org = Organisation.objects.create(name="Other Org", slug="other-org")
    return User.objects.create_user(
        email="other@example.com",
        password="StrongPass123!",
        first_name="Other",
        last_name="User",
        organisation=org,
        org_role="owner",
    )


@pytest.fixture
def zoom_meeting(db, org_owner):
    return Meeting.objects.create(
        title="Zoom Standup",
        platform=Meeting.Platform.ZOOM,
        meeting_url="https://zoom.us/j/123456",
        created_by=org_owner,
        organisation=org_owner.organisation,
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

    def test_individual_can_create_upload_meeting(self, client, individual_user):
        client.force_authenticate(user=individual_user)
        response = client.post(
            reverse("meetings:meeting-list-create"),
            {
                "title":    "My Meeting",
                "platform": "upload",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["data"]["title"] == "My Meeting"

    def test_org_user_can_create_zoom_meeting(self, client, org_owner):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse("meetings:meeting-list-create"),
            {
                "title":       "Standup",
                "platform":    "zoom",
                "meeting_url": "https://zoom.us/j/999",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["platform"] == "zoom"
        assert data["status"]   == "scheduled"

    def test_zoom_without_url_returns_400(self, client, org_owner):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse("meetings:meeting-list-create"),
            {"title": "No URL", "platform": "zoom"},
            format="json",
        )
        assert response.status_code == 400

    def test_upload_with_url_returns_400(self, client, individual_user):
        client.force_authenticate(user=individual_user)
        response = client.post(
            reverse("meetings:meeting-list-create"),
            {
                "title":       "Upload with URL",
                "platform":    "upload",
                "meeting_url": "https://zoom.us/j/123",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_unauthenticated_returns_401(self, client):
        response = client.post(
            reverse("meetings:meeting-list-create"),
            {"title": "Test", "platform": "upload"},
            format="json",
        )
        assert response.status_code == 401

    def test_meeting_scoped_to_org(self, client, org_owner):
        client.force_authenticate(user=org_owner)
        client.post(
            reverse("meetings:meeting-list-create"),
            {
                "title":       "Org Meeting",
                "platform":    "zoom",
                "meeting_url": "https://zoom.us/j/999",
            },
            format="json",
        )
        meeting = Meeting.objects.get(title="Org Meeting")
        assert meeting.organisation == org_owner.organisation

    def test_individual_meeting_has_no_org(self, client, individual_user):
        client.force_authenticate(user=individual_user)
        client.post(
            reverse("meetings:meeting-list-create"),
            {"title": "Solo", "platform": "upload"},
            format="json",
        )
        meeting = Meeting.objects.get(title="Solo")
        assert meeting.organisation is None


# ── List meetings ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestListMeetings:

    def test_org_member_sees_org_meetings(self, client, org_member, zoom_meeting):
        client.force_authenticate(user=org_member)
        response = client.get(reverse("meetings:meeting-list-create"))
        assert response.status_code == 200
        ids = [m["id"] for m in response.json()["data"]]
        assert str(zoom_meeting.id) in ids

    def test_individual_sees_only_own_meetings(
        self, client, individual_user, individual_meeting, zoom_meeting
    ):
        client.force_authenticate(user=individual_user)
        response = client.get(reverse("meetings:meeting-list-create"))
        ids = [m["id"] for m in response.json()["data"]]
        assert str(individual_meeting.id) in ids
        assert str(zoom_meeting.id) not in ids

    def test_other_org_cannot_see_meetings(
        self, client, other_org_user, zoom_meeting
    ):
        client.force_authenticate(user=other_org_user)
        response = client.get(reverse("meetings:meeting-list-create"))
        ids = [m["id"] for m in response.json()["data"]]
        assert str(zoom_meeting.id) not in ids

    def test_archived_meetings_excluded(self, client, org_owner, zoom_meeting):
        zoom_meeting.is_archived = True
        zoom_meeting.save()
        client.force_authenticate(user=org_owner)
        response = client.get(reverse("meetings:meeting-list-create"))
        ids = [m["id"] for m in response.json()["data"]]
        assert str(zoom_meeting.id) not in ids


# ── Get meeting detail ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMeetingDetail:

    def test_owner_can_get_meeting(self, client, org_owner, zoom_meeting):
        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id])
        )
        assert response.status_code == 200
        assert response.json()["data"]["id"] == str(zoom_meeting.id)

    def test_org_member_can_get_meeting(self, client, org_member, zoom_meeting):
        client.force_authenticate(user=org_member)
        response = client.get(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id])
        )
        assert response.status_code == 200

    def test_other_org_cannot_get_meeting(
        self, client, other_org_user, zoom_meeting
    ):
        client.force_authenticate(user=other_org_user)
        response = client.get(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id])
        )
        assert response.status_code == 404


# ── Update meeting ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUpdateMeeting:

    def test_owner_can_update_title(self, client, org_owner, zoom_meeting):
        client.force_authenticate(user=org_owner)
        response = client.patch(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id]),
            {"title": "Updated Title"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["data"]["title"] == "Updated Title"

    def test_member_cannot_update_meeting(self, client, org_member, zoom_meeting):
        client.force_authenticate(user=org_member)
        response = client.patch(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id]),
            {"title": "Hacked"},
            format="json",
        )
        assert response.status_code == 403

    def test_cannot_update_non_scheduled_meeting(
        self, client, org_owner, zoom_meeting
    ):
        zoom_meeting.status = Meeting.Status.RECORDING
        zoom_meeting.save()
        client.force_authenticate(user=org_owner)
        response = client.patch(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id]),
            {"title": "New Title"},
            format="json",
        )
        assert response.status_code == 400


# ── Delete meeting ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDeleteMeeting:

    def test_owner_can_archive_meeting(self, client, org_owner, zoom_meeting):
        client.force_authenticate(user=org_owner)
        response = client.delete(
            reverse("meetings:meeting-detail", args=[zoom_meeting.id])
        )
        assert response.status_code == 200
        zoom_meeting.refresh_from_db()
        assert zoom_meeting.is_archived is True

    def test_member_cannot_delete_meeting(self, client, org_member, zoom_meeting):
        client.force_authenticate(user=org_member)
        response = client.delete(
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
