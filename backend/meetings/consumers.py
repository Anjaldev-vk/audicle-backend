import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser

logger = logging.getLogger("meetings")


def meeting_group_name(meeting_id: str) -> str:
    return f"meeting_{meeting_id}"


class MeetingConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time meeting status updates.
    Connect: ws://<host>/ws/v1/meetings/<meeting_id>/?token=<jwt>
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
            logger.warning("WebSocket rejected — unauthenticated connection attempt")
            await self.close(code=4001)
            return

        self.meeting_id = self.scope["url_route"]["kwargs"]["meeting_id"]
        self.group_name = meeting_group_name(self.meeting_id)

        # Verify user has access to this meeting
        has_access = await self.check_meeting_access(user, self.meeting_id)
        if not has_access:
            logger.warning(
                "WebSocket rejected — user %s has no access to meeting %s",
                user.id,
                self.meeting_id,
            )
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info(
            "WebSocket connected — user %s joined meeting room %s",
            user.id,
            self.meeting_id,
        )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            logger.info("WebSocket disconnected from room %s (code=%s)", self.group_name, close_code)

    # Clients don't send messages — receive is a no-op
    async def receive(self, text_data=None, bytes_data=None):
        pass

    # ── Event handlers (called by channel_layer.group_send) ──────────────────

    async def meeting_status_update(self, event):
        await self.send(text_data=json.dumps({
            "type": "meeting.status_update",
            "meeting_id": event["meeting_id"],
            "status": event["status"],
        }))

    async def transcript_ready(self, event):
        await self.send(text_data=json.dumps({
            "type": "transcript.ready",
            "meeting_id": event["meeting_id"],
            "transcript_id": event["transcript_id"],
            "status": event["status"],
        }))

    async def summary_ready(self, event):
        await self.send(text_data=json.dumps({
            "type": "summary.ready",
            "meeting_id": event["meeting_id"],
            "summary_id": event["summary_id"],
            "status": event["status"],
        }))

    async def embedding_ready(self, event):
        await self.send(text_data=json.dumps({
            "type": "embedding.ready",
            "meeting_id": event["meeting_id"],
        }))

    # ── DB access ─────────────────────────────────────────────────────────────

    @database_sync_to_async
    def check_meeting_access(self, user, meeting_id):
        from meetings.models import Meeting
        from django.db.models import Q
        
        # Check if user is creator or belongs to the organization the meeting is in
        org_ids = user.memberships.values_list('organisation_id', flat=True)
        
        return Meeting.objects.filter(
            Q(pk=meeting_id, is_archived=False) &
            (Q(created_by=user, organisation=None) | Q(organisation_id__in=org_ids))
        ).exists()
