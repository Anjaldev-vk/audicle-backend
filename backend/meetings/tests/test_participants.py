import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from meetings.models import Meeting, MeetingParticipant


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def org_owner(db):
    from accounts.models import Organisation, User
    org  = Organisation.objects.create(name="Acme", slug="acme")
    user = User.objects.create_user(
        email="owner@acme.com",
        password="StrongPass123!",
        first_name="Owner",
        last_name="User",
        organisation=org,
        org_role="owner",
    )
    return user


@pytest.fixture
def org_member(db, org_owner):
    from accounts.models import User
    return User.objects.create_user(
        email="member@acme.com",
        password="StrongPass123!",
        first_name="Member",
        last_name="User",
        organisation=org_owner.organisation,
        org_role="member",
    )


@pytest.fixture
def meeting(db, org_owner):
    return Meeting.objects.create(
        title="Team Standup",
        platform=Meeting.Platform.ZOOM,
        meeting_url="https://zoom.us/j/123456",
        created_by=org_owner,
        organisation=org_owner.organisation,
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

    def test_owner_can_list_participants(self, client, org_owner, meeting, participant):
        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse("meetings:participant-list-create", args=[meeting.id])
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) == 1

    def test_member_can_list_participants(self, client, org_member, meeting, participant):
        client.force_authenticate(user=org_member)
        response = client.get(
            reverse("meetings:participant-list-create", args=[meeting.id])
        )
        assert response.status_code == 200


# ── Add participant ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAddParticipant:

    def test_owner_can_add_participant(self, client, org_owner, meeting):
        client.force_authenticate(user=org_owner)
        response = client.post(
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

    def test_duplicate_email_returns_400(self, client, org_owner, meeting, participant):
        client.force_authenticate(user=org_owner)
        response = client.post(
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
        self, client, org_owner, meeting, participant
    ):
        client.force_authenticate(user=org_owner)
        response = client.delete(
            reverse(
                "meetings:participant-delete",
                args=[meeting.id, participant.id],
            )
        )
        assert response.status_code == 200
        assert not MeetingParticipant.objects.filter(id=participant.id).exists()

    def test_member_cannot_remove_participant(
        self, client, org_member, meeting, participant
    ):
        client.force_authenticate(user=org_member)
        response = client.delete(
            reverse(
                "meetings:participant-delete",
                args=[meeting.id, participant.id],
            )
        )
        assert response.status_code == 403

    def test_remove_nonexistent_participant_returns_404(
        self, client, org_owner, meeting
    ):
        import uuid
        client.force_authenticate(user=org_owner)
        response = client.delete(
            reverse(
                "meetings:participant-delete",
                args=[meeting.id, uuid.uuid4()],
            )
        )
        assert response.status_code == 404
