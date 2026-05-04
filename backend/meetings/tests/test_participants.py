import pytest
from django.urls import reverse
from meetings.models import Meeting, MeetingParticipant


@pytest.fixture
def meeting(db, org_admin, organisation):
    return Meeting.objects.create(
        title="Team Standup",
        platform=Meeting.Platform.ZOOM,
        meeting_url="https://zoom.us/j/123456",
        created_by=org_admin,
        organisation=organisation,
    )


@pytest.fixture
def participant(db, meeting):
    return MeetingParticipant.objects.create(
        meeting=meeting,
        email="guest@example.com",
        name="Guest User",
        role=MeetingParticipant.Role.PARTICIPANT,
    )


# ── List participants ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestListParticipants:

    def test_owner_can_list_participants(self, org_admin_client, meeting, participant):
        response = org_admin_client.get(
            reverse("meetings:participant-list-create", args=[meeting.id])
        )
        assert response.status_code == 200
        results = response.json()["data"].get("results", response.json()["data"])
        assert len(results) >= 1

    def test_member_can_list_participants(self, org_member_client, meeting, participant):
        response = org_member_client.get(
            reverse("meetings:participant-list-create", args=[meeting.id])
        )
        assert response.status_code == 200


# ── Add participant ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAddParticipant:

    def test_owner_can_add_participant(self, org_admin_client, meeting):
        response = org_admin_client.post(
            reverse("meetings:participant-list-create", args=[meeting.id]),
            {
                "email": "new@example.com",
                "name":  "New Person",
                "role":  "participant",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["data"]["email"] == "new@example.com"

    def test_duplicate_email_returns_400(self, org_admin_client, meeting, participant):
        response = org_admin_client.post(
            reverse("meetings:participant-list-create", args=[meeting.id]),
            {
                "email": "guest@example.com",
                "name":  "Duplicate",
                "role":  "participant",
            },
            format="json",
        )
        assert response.status_code == 400


# ── Remove participant ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRemoveParticipant:

    def test_owner_can_remove_participant(
        self, org_admin_client, meeting, participant
    ):
        response = org_admin_client.delete(
            reverse(
                "meetings:participant-delete",
                args=[meeting.id, participant.id],
            )
        )
        assert response.status_code == 200
        assert not MeetingParticipant.objects.filter(id=participant.id).exists()

    def test_member_cannot_remove_participant(
        self, org_member_client, meeting, participant
    ):
        response = org_member_client.delete(
            reverse(
                "meetings:participant-delete",
                args=[meeting.id, participant.id],
            )
        )
        assert response.status_code == 403

    def test_remove_nonexistent_participant_returns_404(
        self, org_admin_client, meeting
    ):
        import uuid
        response = org_admin_client.delete(
            reverse(
                "meetings:participant-delete",
                args=[meeting.id, uuid.uuid4()],
            )
        )
        assert response.status_code == 404
