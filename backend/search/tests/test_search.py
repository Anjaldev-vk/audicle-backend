import pytest
from rest_framework import status

from meetings.models import Meeting
from transcripts.models import Transcript, TranscriptSegment, MeetingSummary


SEARCH_URL = "/api/v1/search/"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def completed_meeting(meeting):
    """Meeting already in completed status."""
    meeting.status = Meeting.Status.COMPLETED
    meeting.save(update_fields=["status"])
    return meeting


@pytest.fixture
def completed_transcript(completed_meeting, user):
    return Transcript.objects.create(
        meeting=completed_meeting,
        organisation=completed_meeting.organisation,
        created_by=user,
        status=Transcript.Status.COMPLETED,
        raw_text="The quarterly revenue targets were discussed at length.",
        language="en",
    )


@pytest.fixture
def completed_summary(completed_meeting, user):
    return MeetingSummary.objects.create(
        meeting=completed_meeting,
        organisation=completed_meeting.organisation,
        created_by=user,
        status=MeetingSummary.Status.COMPLETED,
        summary="Team agreed to increase budget for Q3 marketing campaigns.",
        key_points=["Budget increase approved"],
        action_items=[],
        decisions=["Q3 budget approved"],
        next_steps=["Send updated plan"],
    )


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchAuth:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(SEARCH_URL, {"q": "meeting"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ── Validation ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchValidation:

    def test_missing_query_returns_400(self, auth_client):
        response = auth_client.get(SEARCH_URL)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["success"] is False
        assert data["code"] == "missing_query"

    def test_empty_query_returns_400(self, auth_client):
        response = auth_client.get(SEARCH_URL, {"q": ""})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["code"] == "missing_query"

    def test_single_char_query_returns_400(self, auth_client):
        response = auth_client.get(SEARCH_URL, {"q": "a"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["code"] == "query_too_short"

    def test_invalid_type_returns_400(self, auth_client):
        response = auth_client.get(SEARCH_URL, {"q": "hello", "type": "invalid"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["code"] == "invalid_type"

    def test_valid_query_returns_200(self, auth_client):
        response = auth_client.get(SEARCH_URL, {"q": "anything"})
        assert response.status_code == status.HTTP_200_OK


# ── Meeting search ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchMeetings:

    def test_finds_meeting_by_title(self, auth_client, completed_meeting):
        # Use a word we know is in the title
        word = completed_meeting.title.split()[0]
        response = auth_client.get(SEARCH_URL, {"q": word, "type": "meetings"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["type"] == "meetings"
        ids = [r["id"] for r in data["results"]]
        assert str(completed_meeting.id) in ids

    def test_meeting_result_shape(self, auth_client, completed_meeting):
        word = completed_meeting.title.split()[0]
        response = auth_client.get(SEARCH_URL, {"q": word, "type": "meetings"})

        results = response.json()["data"]["results"]
        assert len(results) >= 1
        result = results[0]

        # Verify all expected keys are present
        assert "type" in result
        assert "id" in result
        assert "title" in result
        assert "status" in result
        assert "platform" in result
        assert "created_at" in result
        # rank must be stripped from response
        assert "rank" not in result

    def test_archived_meeting_excluded(self, auth_client, completed_meeting):
        completed_meeting.is_archived = True
        completed_meeting.save(update_fields=["is_archived"])

        word = completed_meeting.title.split()[0]
        response = auth_client.get(SEARCH_URL, {"q": word, "type": "meetings"})

        ids = [r["id"] for r in response.json()["data"]["results"]]
        assert str(completed_meeting.id) not in ids

    def test_no_match_returns_empty_list(self, auth_client):
        response = auth_client.get(
            SEARCH_URL, {"q": "xyznonexistentword", "type": "meetings"}
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["results"] == []
        assert data["total"] == 0


# ── Transcript search ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchTranscripts:

    def test_finds_transcript_by_content(
        self, auth_client, completed_transcript
    ):
        response = auth_client.get(
            SEARCH_URL, {"q": "quarterly", "type": "transcripts"}
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        ids = [r["id"] for r in data["results"]]
        assert str(completed_transcript.id) in ids

    def test_transcript_result_shape(self, auth_client, completed_transcript):
        response = auth_client.get(
            SEARCH_URL, {"q": "quarterly", "type": "transcripts"}
        )
        results = response.json()["data"]["results"]
        assert len(results) >= 1
        result = results[0]

        assert result["type"] == "transcript"
        assert "id" in result
        assert "meeting_id" in result
        assert "meeting_title" in result
        assert "language" in result
        assert "word_count" in result
        assert "created_at" in result
        assert "rank" not in result

    def test_pending_transcript_excluded(
        self, auth_client, completed_meeting, user, organisation
    ):
        Transcript.objects.create(
            meeting=completed_meeting,
            organisation=organisation,
            created_by=user,
            status=Transcript.Status.PENDING,
            raw_text="quarterly revenue discussed",
        )
        response = auth_client.get(
            SEARCH_URL, {"q": "quarterly", "type": "transcripts"}
        )
        # Only completed transcripts should appear
        for r in response.json()["data"]["results"]:
            assert r["type"] == "transcript"


# ── Summary search ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchSummaries:

    def test_finds_summary_by_content(
        self, auth_client, completed_summary
    ):
        response = auth_client.get(
            SEARCH_URL, {"q": "budget", "type": "summaries"}
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        ids = [r["id"] for r in data["results"]]
        assert str(completed_summary.id) in ids

    def test_summary_result_shape(self, auth_client, completed_summary):
        response = auth_client.get(
            SEARCH_URL, {"q": "budget", "type": "summaries"}
        )
        results = response.json()["data"]["results"]
        assert len(results) >= 1
        result = results[0]

        assert result["type"] == "summary"
        assert "id" in result
        assert "meeting_id" in result
        assert "meeting_title" in result
        assert "created_at" in result
        assert "rank" not in result

    def test_failed_summary_excluded(
        self, auth_client, completed_meeting, user, organisation
    ):
        MeetingSummary.objects.create(
            meeting=completed_meeting,
            organisation=organisation,
            created_by=user,
            status=MeetingSummary.Status.FAILED,
            summary="budget marketing campaigns",
            key_points=[],
            action_items=[],
            decisions=[],
            next_steps=[],
        )
        response = auth_client.get(
            SEARCH_URL, {"q": "budget", "type": "summaries"}
        )
        # Failed summaries must not appear
        for r in response.json()["data"]["results"]:
            assert r["type"] == "summary"


# ── Combined search ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchCombined:

    def test_default_type_is_all(
        self, auth_client, completed_transcript, completed_summary
    ):
        """Omitting type= searches across all resource types."""
        response = auth_client.get(SEARCH_URL, {"q": "quarterly"})
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["type"] == "all"

    def test_response_envelope(self, auth_client):
        response = auth_client.get(SEARCH_URL, {"q": "hello"})
        data = response.json()

        assert "success" in data
        assert "data" in data
        assert "query" in data["data"]
        assert "type" in data["data"]
        assert "total" in data["data"]
        assert "results" in data["data"]

    def test_total_matches_results_length(
        self, auth_client, completed_transcript, completed_summary
    ):
        response = auth_client.get(SEARCH_URL, {"q": "quarterly"})
        data = response.json()["data"]
        assert data["total"] == len(data["results"])


# ── Workspace isolation ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSearchIsolation:

    def test_other_org_results_not_visible(
        self,
        api_client,
        completed_meeting,
        create_user,
    ):
        """A user in a different org must not see the first user's meetings."""
        other_user = create_user(email="outsider@example.com")
        api_client.force_authenticate(user=other_user)

        word = completed_meeting.title.split()[0]
        response = api_client.get(SEARCH_URL, {"q": word, "type": "meetings"})

        assert response.status_code == status.HTTP_200_OK
        ids = [r["id"] for r in response.json()["data"]["results"]]
        assert str(completed_meeting.id) not in ids
