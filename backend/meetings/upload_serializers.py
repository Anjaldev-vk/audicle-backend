from django.conf import settings
from rest_framework import serializers


class RequestUploadURLSerializer(serializers.Serializer):
    """
    Validates the request for a presigned S3 upload URL.

    Client sends this BEFORE uploading so Django can:
    1. Validate the file type is allowed
    2. Validate the file size is within limits
    3. Generate a unique S3 key
    4. Return a presigned URL for direct upload
    """
    filename = serializers.CharField(
        max_length=255,
        required=False,
        help_text="Original filename e.g. standup.mp3",
    )
    content_type = serializers.CharField(
        max_length=100,
        required=False,
        help_text="MIME type e.g. audio/mpeg",
    )
    file_size = serializers.IntegerField(
        min_value=1,
        required=False,
        help_text="File size in bytes",
    )

    # Aliases for frontend flexibility
    name      = serializers.CharField(required=False, write_only=True)
    file_name = serializers.CharField(required=False, write_only=True)
    type      = serializers.CharField(required=False, write_only=True)
    size      = serializers.IntegerField(required=False, write_only=True)

    def to_internal_value(self, data):
        # Map aliases before validation
        if hasattr(data, 'dict'): 
            data = data.dict()
        elif isinstance(data, dict):
            data = data.copy()
        else:
            # If it's a list or other type, let super() handle it or raise
            return super().to_internal_value(data)

        # Handle nested 'file' object if present
        if 'file' in data and isinstance(data['file'], dict):
            file_data = data['file']
            if 'name' in file_data:      data['filename']     = file_data['name']
            if 'file_name' in file_data: data['filename']     = file_data['file_name']
            if 'type' in file_data:      data['content_type'] = file_data['type']
            if 'size' in file_data:      data['file_size']    = file_data['size']

        if 'name' in data and 'filename' not in data:
            data['filename'] = data['name']
        if 'file_name' in data and 'filename' not in data:
            data['filename'] = data['file_name']
        if 'type' in data and 'content_type' not in data:
            data['content_type'] = data['type']
        if 'size' in data and 'file_size' not in data:
            data['file_size'] = data['size']
            
        return super().to_internal_value(data)

    def validate(self, attrs):
        # Check required fields
        missing = []
        if not attrs.get('filename'):     missing.append('filename')
        if not attrs.get('content_type'): missing.append('content_type')

        if missing:
            raise serializers.ValidationError({
                field: "This field is required." for field in missing
            })

        return attrs

    def validate_content_type(self, value):
        """
        Only allow audio and video formats.
        Prevents users from uploading executables, scripts etc.
        """
        if not value:
            return value
            
        value = value.lower().strip()
        allowed = settings.AWS_ALLOWED_UPLOAD_TYPES
        if value not in allowed:
            raise serializers.ValidationError(
                f"Unsupported file type '{value}'. "
                f"Allowed types: {', '.join(allowed.keys())}"
            )
        return value

    def validate_file_size(self, value):
        """
        Enforce 500MB maximum.
        Prevents storage abuse and keeps transcription times reasonable.
        """
        max_size = settings.AWS_MAX_UPLOAD_SIZE
        if value > max_size:
            mb = value // (1024 * 1024)
            raise serializers.ValidationError(
                f"File size {mb}MB exceeds the 500MB limit."
            )
        return value

    def validate_filename(self, value):
        """
        Strip path components — prevent directory traversal.
        '../../etc/passwd.mp3' → 'passwd.mp3'
        """
        # Take only the filename, strip any path
        import os
        return os.path.basename(value)


class ConfirmUploadSerializer(serializers.Serializer):
    """
    Client sends this AFTER successfully uploading to S3.

    Django uses the s3_key to:
    1. Verify the file actually exists in S3
    2. Save the key to meeting.audio_s3_key
    3. Fire the Kafka message for Whisper transcription
    """
    s3_key = serializers.CharField(
        max_length=500,
        help_text="The S3 key returned from request-url/ endpoint",
    )

    def validate_s3_key(self, value):
        """
        S3 key must start with meetings/ to prevent clients
        from pointing to arbitrary paths in our bucket.

        Valid:   "meetings/uuid/audio/uuid.mp3"
        Invalid: "../../secret/file.mp3"
        Invalid: "other-app/data/file.mp3"
        """
        if not value.startswith("meetings/"):
            raise serializers.ValidationError(
                "Invalid S3 key format."
            )
        # Must end with a valid audio extension
        allowed_extensions = (".mp3", ".mp4", ".wav", ".m4a", ".webm", ".mov")
        if not value.endswith(allowed_extensions):
            raise serializers.ValidationError(
                "Invalid file extension in S3 key."
            )
        return value
