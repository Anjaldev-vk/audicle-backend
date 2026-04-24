import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from unittest.mock import patch

from meetings.models import Meeting


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def org_owner(db):
    from accounts.models import Organisation, User
    org = Organisation.objects.create(
        name="Upload Org",
        slug="upload-org",
    )
    return User.objects.create_user(
        email="owner@upload.com",
        password="StrongPass123!",
        first_name="Upload",
        last_name="Owner",
        organisation=org,
        org_role="owner",
    )


@pytest.fixture
def upload_meeting(db, org_owner):
    return Meeting.objects.create(
        title="Upload Meeting",
        platform=Meeting.Platform.UPLOAD,
        created_by=org_owner,
        organisation=org_owner.organisation,
    )


@pytest.fixture
def zoom_meeting(db, org_owner):
    return Meeting.objects.create(
        title="Zoom Meeting",
        platform=Meeting.Platform.ZOOM,
        meeting_url="https://zoom.us/j/123456",
        created_by=org_owner,
        organisation=org_owner.organisation,
    )


VALID_UPLOAD_PAYLOAD = {
    "filename":     "standup.mp3",
    "content_type": "audio/mpeg",
    "file_size":    10 * 1024 * 1024,   # 10MB
}

MOCK_S3_RESULT = {
    "upload_url": "https://s3.amazonaws.com/fake-url",
    "s3_key":     "meetings/uuid/audio/uuid.mp3",
    "expires_in": 900,
}


# ── Request Upload URL ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRequestUploadURL:

    @patch("meetings.upload_views.generate_presigned_upload_url")
    def test_returns_presigned_url(
        self, mock_s3, client, org_owner, upload_meeting
    ):
        mock_s3.return_value = MOCK_S3_RESULT
        client.force_authenticate(user=org_owner)

        response = client.post(
            reverse("meetings:upload-request-url", args=[upload_meeting.id]),
            VALID_UPLOAD_PAYLOAD,
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "upload_url" in data
        assert "s3_key"     in data
        assert "expires_in" in data

    @patch("meetings.upload_views.generate_presigned_upload_url")
    def test_response_format_is_standard(
        self, mock_s3, client, org_owner, upload_meeting
    ):
        mock_s3.return_value = MOCK_S3_RESULT
        client.force_authenticate(user=org_owner)

        response = client.post(
            reverse("meetings:upload-request-url", args=[upload_meeting.id]),
            VALID_UPLOAD_PAYLOAD,
            format="json",
        )
        body = response.json()
        assert body["success"] is True
        assert "message" in body
        assert "data"    in body

    def test_zoom_meeting_returns_400(self, client, org_owner, zoom_meeting):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse("meetings:upload-request-url", args=[zoom_meeting.id]),
            VALID_UPLOAD_PAYLOAD,
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_platform"

    def test_unsupported_content_type_returns_400(
        self, client, org_owner, upload_meeting
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse("meetings:upload-request-url", args=[upload_meeting.id]),
            {
                "filename":     "document.pdf",
                "content_type": "application/pdf",
                "file_size":    1024,
            },
            format="json",
        )
        assert response.status_code == 400

    def test_file_too_large_returns_400(
        self, client, org_owner, upload_meeting
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse("meetings:upload-request-url", args=[upload_meeting.id]),
            {
                "filename":     "huge.mp3",
                "content_type": "audio/mpeg",
                "file_size":    600 * 1024 * 1024,   # 600MB
            },
            format="json",
        )
        assert response.status_code == 400

    def test_unauthenticated_returns_401(self, client, upload_meeting):
        response = client.post(
            reverse("meetings:upload-request-url", args=[upload_meeting.id]),
            VALID_UPLOAD_PAYLOAD,
            format="json",
        )
        assert response.status_code == 401

    def test_completed_meeting_returns_400(
        self, client, org_owner, upload_meeting
    ):
        upload_meeting.status = Meeting.Status.COMPLETED
        upload_meeting.save()
        client.force_authenticate(user=org_owner)

        response = client.post(
            reverse("meetings:upload-request-url", args=[upload_meeting.id]),
            VALID_UPLOAD_PAYLOAD,
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_status"

    @patch("meetings.upload_views.generate_presigned_upload_url")
    def test_s3_failure_returns_503(
        self, mock_s3, client, org_owner, upload_meeting
    ):
        mock_s3.return_value = None
        client.force_authenticate(user=org_owner)

        response = client.post(
            reverse("meetings:upload-request-url", args=[upload_meeting.id]),
            VALID_UPLOAD_PAYLOAD,
            format="json",
        )
        assert response.status_code == 503
        assert response.json()["code"] == "s3_error"


# ── Confirm Upload ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestConfirmUpload:

    @patch("meetings.upload_views.send_transcription_task")
    @patch("meetings.upload_views.check_s3_object_exists")
    def test_confirm_saves_key_and_fires_kafka(
        self, mock_exists, mock_kafka, client, org_owner, upload_meeting
    ):
        mock_exists.return_value = True
        client.force_authenticate(user=org_owner)

        response = client.post(
            reverse("meetings:upload-confirm", args=[upload_meeting.id]),
            {"s3_key": "meetings/uuid/audio/uuid.mp3"},
            format="json",
        )
        assert response.status_code == 200
        upload_meeting.refresh_from_db()
        assert upload_meeting.audio_s3_key == "meetings/uuid/audio/uuid.mp3"
        assert upload_meeting.status       == Meeting.Status.PROCESSING
        mock_kafka.assert_called_once()

    @patch("meetings.upload_views.check_s3_object_exists")
    def test_file_not_in_s3_returns_400(
        self, mock_exists, client, org_owner, upload_meeting
    ):
        mock_exists.return_value = False
        client.force_authenticate(user=org_owner)

        response = client.post(
            reverse("meetings:upload-confirm", args=[upload_meeting.id]),
            {"s3_key": "meetings/uuid/audio/uuid.mp3"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "file_not_found"

    def test_invalid_s3_key_returns_400(
        self, client, org_owner, upload_meeting
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse("meetings:upload-confirm", args=[upload_meeting.id]),
            {"s3_key": "../../etc/passwd"},
            format="json",
        )
        assert response.status_code == 400

    @patch("meetings.upload_views.send_transcription_task")
    @patch("meetings.upload_views.check_s3_object_exists")
    def test_kafka_failure_reverts_to_failed(
        self, mock_exists, mock_kafka, client, org_owner, upload_meeting
    ):
        mock_exists.return_value = True
        mock_kafka.side_effect   = Exception("Kafka unavailable")
        client.force_authenticate(user=org_owner)

        response = client.post(
            reverse("meetings:upload-confirm", args=[upload_meeting.id]),
            {"s3_key": "meetings/uuid/audio/uuid.mp3"},
            format="json",
        )
        assert response.status_code == 503
        upload_meeting.refresh_from_db()
        assert upload_meeting.status == Meeting.Status.FAILED


# ── Get Download URL ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetDownloadURL:

    @patch("meetings.upload_views.generate_presigned_download_url")
    def test_returns_download_url(
        self, mock_s3, client, org_owner, upload_meeting
    ):
        mock_s3.return_value = "https://s3.amazonaws.com/fake-download"
        upload_meeting.audio_s3_key = "meetings/uuid/audio/uuid.mp3"
        upload_meeting.save()

        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse("meetings:upload-download-url", args=[upload_meeting.id])
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "download_url" in data
        assert "expires_in"   in data

    def test_no_audio_returns_404(self, client, org_owner, upload_meeting):
        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse("meetings:upload-download-url", args=[upload_meeting.id])
        )
        assert response.status_code == 404
        assert response.json()["code"] == "no_audio"

    @patch("meetings.upload_views.generate_presigned_download_url")
    def test_s3_failure_returns_503(
        self, mock_s3, client, org_owner, upload_meeting
    ):
        mock_s3.return_value = None
        upload_meeting.audio_s3_key = "meetings/uuid/audio/uuid.mp3"
        upload_meeting.save()

        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse("meetings:upload-download-url", args=[upload_meeting.id])
        )
        assert response.status_code == 503
        assert response.json()["code"] == "s3_error"

    def test_unauthenticated_returns_401(self, client, upload_meeting):
        response = client.get(
            reverse("meetings:upload-download-url", args=[upload_meeting.id])
        )
        assert response.status_code == 401