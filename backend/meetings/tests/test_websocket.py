import pytest
from channels.testing import WebsocketCommunicator
from channels.layers import get_channel_layer
from channels.db import database_sync_to_async
from asgiref.sync import async_to_sync
from config.asgi import application
from accounts.models import User, Organisation
from meetings.consumers import meeting_group_name

# Helpers
def make_user(email="ws@test.com"):
    org = Organisation.objects.create(name="WS Org", slug="ws-org")
    return User.objects.create_user(
        email=email,
        password="pass",
        organisation=org,
        is_verified=True,
        first_name="WS",
        last_name="User",
        org_role=User.OrgRole.MEMBER
    ), org

def get_jwt(user):
    from rest_framework_simplejwt.tokens import RefreshToken
    return str(RefreshToken.for_user(user).access_token)

@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
class TestMeetingConsumer:

    async def test_connect_authenticated(self):
        user, org = await database_sync_to_async(make_user)()
        from meetings.models import Meeting
        meeting = await database_sync_to_async(Meeting.objects.create)(
            title="Test", created_by=user, organisation=org, platform="zoom"
        )
        token = await database_sync_to_async(get_jwt)(user)
        comm = WebsocketCommunicator(
            application, f"/ws/v1/meetings/{meeting.id}/?token={token}"
        )
        connected, _ = await comm.connect()
        assert connected
        await comm.disconnect()

    async def test_reject_unauthenticated(self):
        comm = WebsocketCommunicator(
            application, "/ws/v1/meetings/00000000-0000-0000-0000-000000000001/?token=bad"
        )
        connected, code = await comm.connect()
        assert not connected or code == 4001

    async def test_receives_status_push(self):
        user, org = await database_sync_to_async(make_user)("ws2@test.com")
        from meetings.models import Meeting
        meeting = await database_sync_to_async(Meeting.objects.create)(
            title="Push Test", created_by=user, organisation=org, platform="zoom"
        )
        token = await database_sync_to_async(get_jwt)(user)
        comm = WebsocketCommunicator(
            application, f"/ws/v1/meetings/{meeting.id}/?token={token}"
        )
        await comm.connect()
        channel_layer = get_channel_layer()
        group = meeting_group_name(str(meeting.id))
        await channel_layer.group_send(group, {
            "type": "meeting.status_update",
            "meeting_id": str(meeting.id),
            "status": "recording",
        })
        response = await comm.receive_json_from()
        assert response["type"] == "meeting.status_update"
        assert response["status"] == "recording"
        await comm.disconnect()
