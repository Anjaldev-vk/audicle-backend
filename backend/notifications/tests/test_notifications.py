import pytest
from unittest.mock import patch, MagicMock
from django.urls import reverse
from rest_framework.test import APIClient
from accounts.models import User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='test@example.com',
        password='testpass123',
        first_name='Test',
        last_name='User',
        is_verified=True,
    )


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


MOCK_NOTIFICATION = {
    'user_id':    'user-123',
    'sk':         '2026-05-04T10:00:00+00:00#notif-123',
    'id':         'notif-123',
    'type':       'transcription_done',
    'title':      'Transcript ready',
    'message':    'Your transcript is ready',
    'is_read':    'false',
    'created_at': '2026-05-04T10:00:00+00:00',
    'metadata':   {},
}


@patch('notifications.views.repository.get_notifications')
@patch('notifications.views.repository.get_unread_count')
def test_list_notifications(mock_unread, mock_get, auth_client):
    mock_get.return_value = {'items': [MOCK_NOTIFICATION], 'last_key': None}
    mock_unread.return_value = 1

    res = auth_client.get('/api/v1/notifications/')
    assert res.status_code == 200
    data = res.json()['data']
    assert len(data['results']) == 1
    assert data['unread_count'] == 1


@patch('notifications.views.repository.mark_as_read')
def test_mark_as_read(mock_mark, auth_client):
    res = auth_client.patch(
        '/api/v1/notifications/notif-123/read/',
        {'sk': '2026-05-04T10:00:00+00:00#notif-123'},
        format='json',
    )
    assert res.status_code == 200
    mock_mark.assert_called_once()


@patch('notifications.views.repository.mark_all_as_read')
def test_mark_all_as_read(mock_mark, auth_client):
    mock_mark.return_value = 5
    res = auth_client.patch('/api/v1/notifications/read-all/')
    assert res.status_code == 200


@patch('notifications.views.repository.delete_notification')
def test_delete_notification(mock_delete, auth_client):
    res = auth_client.delete(
        '/api/v1/notifications/notif-123/',
        {'sk': '2026-05-04T10:00:00+00:00#notif-123'},
        format='json',
    )
    assert res.status_code == 200
    mock_delete.assert_called_once()


def test_mark_read_missing_sk(auth_client):
    res = auth_client.patch(
        '/api/v1/notifications/notif-123/read/',
        {},
        format='json',
    )
    assert res.status_code == 400


@patch('notifications.tasks.create_notification')
@patch('notifications.tasks._push_via_websocket')
def test_notify_transcription_done_task(mock_push, mock_create):
    mock_create.return_value = MOCK_NOTIFICATION
    from notifications.tasks import notify_transcription_done
    notify_transcription_done(
        user_id='user-123',
        meeting_id='meeting-456',
        meeting_title='Team standup',
    )
    mock_create.assert_called_once()
    mock_push.assert_called_once()
