import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIClient
from accounts.models import User, Organisation, Membership
from analytics.constants import EventType


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
def admin_user(db):
    return User.objects.create_user(
        email='admin@example.com',
        password='testpass123',
        first_name='Admin',
        last_name='User',
        is_verified=True,
    )


@pytest.fixture
def org(admin_user):
    org = Organisation.objects.create(
        name='Test Org', slug='test-org'
    )
    Membership.objects.create(
        user=admin_user, organisation=org, role='admin'
    )
    return org


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def admin_client(admin_user, org):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    client.defaults['HTTP_X_WORKSPACE_ID'] = str(org.id)
    return client


MOCK_EVENTS = [
    {
        'workspace_id': 'ws-123',
        'sk':           'meeting_completed#2026-05-01T10:00:00+00:00#abc',
        'id':           'abc',
        'event_type':   EventType.MEETING_COMPLETED,
        'user_id':      'user-123',
        'created_at':   '2026-05-01T10:00:00+00:00',
        'metadata':     {'duration_seconds': 3600},
    },
    {
        'workspace_id': 'ws-123',
        'sk':           'meeting_completed#2026-05-02T10:00:00+00:00#def',
        'id':           'def',
        'event_type':   EventType.MEETING_COMPLETED,
        'user_id':      'user-123',
        'created_at':   '2026-05-02T10:00:00+00:00',
        'metadata':     {'duration_seconds': 1800},
    },
]


# ── Overview ─────────────────────────────────────────────────────

@patch('analytics.views.build_overview')
def test_overview_personal(mock_overview, auth_client):
    mock_overview.return_value = {
        'period_days':            30,
        'meetings_created':       5,
        'meetings_completed':     4,
        'avg_duration_seconds':   2700,
        'avg_duration_minutes':   45.0,
        'transcriptions_done':    4,
        'summaries_done':         4,
        'action_items_created':   10,
        'action_items_completed': 7,
        'action_completion_rate': 70.0,
        'rag_queries':            12,
        'bot_joins':              4,
        'bot_success_rate':       100.0,
    }
    res = auth_client.get('/api/v1/analytics/overview/')
    assert res.status_code == 200
    data = res.json()['data']
    assert data['meetings_completed'] == 4
    assert data['avg_duration_minutes'] == 45.0
    assert data['action_completion_rate'] == 70.0


@patch('analytics.views.build_overview')
def test_overview_with_period(mock_overview, auth_client):
    mock_overview.return_value = {'period_days': 7}
    res = auth_client.get('/api/v1/analytics/overview/?period=7d')
    assert res.status_code == 200
    mock_overview.assert_called_once()
    args = mock_overview.call_args
    assert args[0][1] == 7


def test_overview_requires_auth(db):
    client = APIClient()
    res = client.get('/api/v1/analytics/overview/')
    assert res.status_code == 401


# ── Meetings chart ───────────────────────────────────────────────

@patch('analytics.views.build_meetings_chart')
def test_meetings_chart(mock_chart, auth_client):
    mock_chart.return_value = [
        {'date': '2026-05-01', 'count': 2},
        {'date': '2026-05-02', 'count': 1},
    ]
    res = auth_client.get('/api/v1/analytics/meetings/?period=7d')
    assert res.status_code == 200
    data = res.json()['data']
    assert 'chart' in data
    assert len(data['chart']) == 2
    assert data['period_days'] == 7


# ── Activity chart ───────────────────────────────────────────────

@patch('analytics.views.build_activity_chart')
def test_activity_chart(mock_activity, auth_client):
    mock_activity.return_value = {
        EventType.MEETING_COMPLETED: {'2026-05-01': 2},
        EventType.RAG_QUERY:         {'2026-05-01': 5},
    }
    res = auth_client.get('/api/v1/analytics/activity/')
    assert res.status_code == 200
    data = res.json()['data']
    assert 'activity' in data


# ── Team overview (admin only) ───────────────────────────────────

@patch('analytics.views.build_team_overview')
def test_team_overview_admin(mock_team, admin_client):
    mock_team.return_value = {
        'period_days':      30,
        'meetings_created': 20,
        'total_events':     150,
        'members_activity': {},
    }
    res = admin_client.get('/api/v1/analytics/team/overview/')
    assert res.status_code == 200
    assert res.json()['data']['meetings_created'] == 20


def test_team_overview_requires_org(auth_client):
    res = auth_client.get('/api/v1/analytics/team/overview/')
    assert res.status_code in [400, 403]


def test_team_overview_requires_admin(db, user, org):
    # Regular member — not admin
    Membership.objects.create(
        user=user, organisation=org, role='member'
    )
    client = APIClient()
    client.force_authenticate(user=user)
    client.defaults['HTTP_X_WORKSPACE_ID'] = str(org.id)
    res = client.get('/api/v1/analytics/team/overview/')
    assert res.status_code == 403


# ── Team members (admin only) ────────────────────────────────────

@patch('analytics.views.build_team_members')
def test_team_members(mock_members, admin_client):
    mock_members.return_value = [
        {
            'user_id':            'user-1',
            'name':               'John',
            'email':              'john@test.com',
            'role':               'member',
            'meetings_completed': 5,
            'action_items_done':  3,
            'rag_queries':        2,
            'total_events':       10,
        }
    ]
    res = admin_client.get('/api/v1/analytics/team/members/')
    assert res.status_code == 200
    data = res.json()['data']
    assert len(data['members']) == 1
    assert data['members'][0]['name'] == 'John'


# ── Repository unit tests ────────────────────────────────────────

def test_group_by_day():
    from analytics.repository import group_by_day
    events = [
        {'created_at': '2026-05-01T10:00:00+00:00'},
        {'created_at': '2026-05-01T12:00:00+00:00'},
        {'created_at': '2026-05-02T10:00:00+00:00'},
    ]
    result = group_by_day(events)
    assert result['2026-05-01'] == 2
    assert result['2026-05-02'] == 1


def test_group_by_user():
    from analytics.repository import group_by_user
    events = [
        {'user_id': 'user-1'},
        {'user_id': 'user-1'},
        {'user_id': 'user-2'},
    ]
    result = group_by_user(events)
    assert result['user-1'] == 2
    assert result['user-2'] == 1


def test_average_metadata_value():
    from analytics.repository import average_metadata_value
    events = [
        {'metadata': {'duration_seconds': 3600}},
        {'metadata': {'duration_seconds': 1800}},
        {'metadata': {'duration_seconds': 2700}},
    ]
    result = average_metadata_value(events, 'duration_seconds')
    assert result == 2700.0


def test_average_metadata_value_empty():
    from analytics.repository import average_metadata_value
    result = average_metadata_value([], 'duration_seconds')
    assert result == 0


# ── Task unit tests ──────────────────────────────────────────────

@patch('analytics.tasks.write_event')
def test_track_transcription_done(mock_write):
    from analytics.tasks import track_transcription_done
    track_transcription_done(
        meeting_id='mtg-123',
        user_id='user-123',
        workspace_id='ws-123',
    )
    mock_write.assert_called_once_with(
        workspace_id='ws-123',
        event_type=EventType.TRANSCRIPTION_DONE,
        user_id='user-123',
        metadata={'meeting_id': 'mtg-123'},
    )


@patch('analytics.tasks.write_event')
def test_track_rag_query(mock_write):
    from analytics.tasks import track_rag_query
    track_rag_query(
        user_id='user-123',
        workspace_id='ws-123',
        session_id='session-456',
    )
    mock_write.assert_called_once()
