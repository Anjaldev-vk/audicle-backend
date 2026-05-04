import logging

from transcripts.models import Transcript

logger = logging.getLogger("transcripts")


def get_transcript_for_meeting(meeting_id: str, user, organisation):
    """
    Returns the Transcript for a meeting scoped to the user's tenant.
    Returns None if not found or access denied.
    """
    try:
        transcript = Transcript.objects.select_related(
            "meeting",
            "organisation",
            "created_by",
        ).get(meeting__id=meeting_id)

        # Tenant isolation check
        if organisation:
            if transcript.organisation != organisation:
                return None
        else:
            if transcript.created_by != user:
                return None

        return transcript

    except Transcript.DoesNotExist:
        return None
