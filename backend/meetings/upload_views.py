import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config import settings
from meetings.models import Meeting
from meetings.upload_serializers import (
    ConfirmUploadSerializer,
    RequestUploadURLSerializer,
)
from meetings.utils import get_meeting_or_404
from utils.kafka_producer import send_transcription_task
from utils.response import error_response, success_response
from utils.s3 import (
    check_s3_object_exists,
    generate_presigned_download_url,
    generate_presigned_upload_url,
)

logger = logging.getLogger("meetings")


class RequestUploadURLView(APIView):
    """
    POST /api/v1/meetings/<id>/upload/request-url/

    Step 1 of the upload flow.
    Client sends file metadata, Django returns a presigned S3 URL.
    Client then uploads DIRECTLY to S3 — Django never sees the file.

    Only available for platform=upload meetings.
    Cannot re-upload if meeting is already processing or completed.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, meeting_id):
        try:
            # 1. Find the meeting (tenant scoped)
            meeting = get_meeting_or_404(meeting_id, request.user)
            if not meeting:
                return error_response(
                    message="Meeting not found.",
                    code="not_found",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

            # 2. Only manual upload meetings support file upload
            if meeting.platform != Meeting.Platform.UPLOAD:
                return error_response(
                    message="File upload is only available for manual upload meetings.",
                    code="invalid_platform",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            # 3. Cannot upload if already being processed
            if meeting.status in (
                Meeting.Status.PROCESSING,
                Meeting.Status.COMPLETED,
            ):
                return error_response(
                    message=f"Cannot upload — meeting is currently '{meeting.get_status_display()}'.",
                    code="invalid_status",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            # 4. Validate request body through serializer
            serializer = RequestUploadURLSerializer(data=request.data)
            if not serializer.is_valid():
                logger.error(
                    "Upload URL request validation failed for meeting %s: %s (Raw data: %s)",
                    meeting.id,
                    serializer.errors,
                    request.data
                )
                return error_response(
                    message="A validation error occurred.",
                    code="validation_error",
                    errors=serializer.errors,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            # 5. Generate presigned URL from S3
            result = generate_presigned_upload_url(
                meeting_id   = str(meeting.id),
                filename     = serializer.validated_data.get("filename", ""),
                content_type = serializer.validated_data.get("content_type", "application/octet-stream"),
            )

            # 6. Handle S3 failure
            if not result:
                return error_response(
                    message="Could not generate upload URL. Please try again.",
                    code="s3_error",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            logger.info(
                "Presigned upload URL generated for meeting %s by %s",
                meeting.id,
                request.user.email,
            )

            # Align with frontend expectation: 
            # 1. Nest in 'data'
            # 2. Provide 'url' alias for 'upload_url'
            return Response(
                {
                    "success": True,
                    "message": "Upload URL generated successfully.",
                    "data": {
                        "url": result["upload_url"],
                        "key": result["s3_key"],
                        "content_type": serializer.validated_data.get("content_type"),
                        **result
                    }
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.exception("FATAL error in RequestUploadURLView for meeting %s", meeting_id)
            return error_response(
                message="An internal server error occurred while preparing your upload. Please check backend logs.",
                code="server_error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ConfirmUploadView(APIView):
    """
    POST /api/v1/meetings/<id>/upload/confirm/

    Step 2 of the upload flow.
    Client calls this AFTER successfully uploading to S3.

    Django will:
    1. Verify the file actually exists in S3
    2. Save the s3_key to meeting.audio_s3_key
    3. Set meeting.status = processing
    4. Fire Kafka message → Whisper worker picks it up
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, meeting_id):

        # 1. Find the meeting
        meeting = get_meeting_or_404(meeting_id, request.user)
        if not meeting:
            return error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # 2. Validate the s3_key through serializer
        serializer = ConfirmUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        s3_key = serializer.validated_data["s3_key"]

        # 3. Verify the file actually exists in S3
        #    Prevents clients from confirming a fake s3_key
        #    without actually uploading anything
        if not check_s3_object_exists(s3_key):
            return error_response(
                message="File not found in storage. Please upload the file first.",
                code="file_not_found",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # 4. Save S3 key and update status
        meeting.audio_s3_key = s3_key
        meeting.status       = Meeting.Status.PROCESSING
        meeting.save(update_fields=["audio_s3_key", "status"])

        # 5. Fire Kafka message → Whisper worker
        try:
            send_transcription_task(
                meeting_id = str(meeting.id),
                file_path  = s3_key,
                user_id    = str(request.user.id),
            )
            logger.info(
                "Kafka transcription task fired for meeting %s s3_key %s",
                meeting.id,
                s3_key,
            )
        except Exception as exc:
            # Kafka failed — revert status so user can retry
            meeting.status = Meeting.Status.FAILED
            meeting.save(update_fields=["status"])
            logger.error(
                "Kafka task failed for meeting %s: %s",
                meeting.id,
                exc,
            )
            return error_response(
                message="Upload confirmed but transcription could not be queued. Please retry.",
                code="kafka_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return success_response(
            message="Upload confirmed. Transcription has been queued.",
            data={
                "meeting_id": str(meeting.id),
                "status":     meeting.status,
                "s3_key":     s3_key,
            },
            status_code=status.HTTP_200_OK,
        )


class GetDownloadURLView(APIView):
    """
    GET /api/v1/meetings/<id>/upload/download-url/

    Returns a presigned S3 GET URL for playing or downloading
    the meeting recording.

    URL expires in 1 hour. Frontend should request a fresh URL
    if the user wants to play the recording after expiry.
    Django never serves the file — it goes directly from S3 to browser.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, meeting_id):

        # 1. Find the meeting
        meeting = get_meeting_or_404(meeting_id, request.user)
        if not meeting:
            return error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # 2. Check audio exists
        if not meeting.audio_s3_key:
            return error_response(
                message="No audio file has been uploaded for this meeting.",
                code="no_audio",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # 3. Generate presigned download URL
        download_url = generate_presigned_download_url(meeting.audio_s3_key)

        if not download_url:
            return error_response(
                message="Could not generate download URL. Please try again.",
                code="s3_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        logger.info(
            "Presigned download URL generated for meeting %s by %s",
            meeting.id,
            request.user.email,
        )

        return success_response(
            message="Download URL generated successfully.",
            data={
                "download_url": download_url,
                "expires_in":   settings.AWS_PRESIGNED_DOWNLOAD_EXPIRY,
            },
            status_code=status.HTTP_200_OK,
        )