import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from accounts.models import User, Organisation, Membership
from meetings.models import Meeting
from action_items.models import ActionItem


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='test@example.com',
        password='testpass123',
        is_verified=True,
        first_name='Test',
        last_name='User',
    )


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def meeting(user):
    return Meeting.objects.create(
        title='Team standup',
        platform='zoom',
        created_by=user,
        status='completed',
    )


@pytest.fixture
def action_item(user, meeting):
    return ActionItem.objects.create(
        meeting=meeting,
        created_by=user,
        text='Follow up with the client',
        status=ActionItem.Status.OPEN,
    )


# ── List ────────────────────────────────────────────────────────

def test_list_action_items_empty(auth_client, meeting):
    url = reverse('meetings:meeting-action-item-list-create', kwargs={'meeting_id': meeting.id})
    res = auth_client.get(url)
    assert res.status_code == 200
    assert res.json()['data']['results'] == []


def test_list_action_items(auth_client, meeting, action_item):
    url = reverse('meetings:meeting-action-item-list-create', kwargs={'meeting_id': meeting.id})
    res = auth_client.get(url)
    assert res.status_code == 200
    data = res.json()['data']['results']
    assert len(data) == 1
    assert data[0]['text'] == action_item.text
    assert data[0]['id'] == str(action_item.id)


# ── Create ──────────────────────────────────────────────────────

def test_create_action_item(auth_client, meeting):
    url = reverse('meetings:meeting-action-item-list-create', kwargs={'meeting_id': meeting.id})
    payload = {
        'text': 'Send proposal by Monday',
    }
    res = auth_client.post(url, payload, format='json')
    assert res.status_code == 201
    assert res.json()['data']['text'] == 'Send proposal by Monday'
    assert ActionItem.objects.count() == 1


def test_create_action_item_with_due_date(auth_client, meeting):
    url = reverse('meetings:meeting-action-item-list-create', kwargs={'meeting_id': meeting.id})
    payload = {
        'text': 'Urgent task',
        'due_date': '2025-12-31',
    }
    res = auth_client.post(url, payload, format='json')
    assert res.status_code == 201
    assert res.json()['data']['due_date'] == '2025-12-31'


def test_create_action_item_missing_text(auth_client, meeting):
    url = reverse('meetings:meeting-action-item-list-create', kwargs={'meeting_id': meeting.id})
    res = auth_client.post(url, {}, format='json')
    assert res.status_code == 400


# ── Detail (Update/Delete) ───────────────────────────────────────

def test_get_action_item_detail_not_implemented(auth_client, action_item):
    url = '/api/v1/action-items/%s/' % action_item.id
    res = auth_client.patch(url, {'status': 'done'}, format='json')
    assert res.status_code == 200
    assert res.json()['data']['status'] == 'done'
    action_item.refresh_from_db()
    assert action_item.status == 'done'


def test_delete_action_item(auth_client, action_item):
    url = '/api/v1/action-items/%s/' % action_item.id
    res = auth_client.delete(url)
    assert res.status_code == 200
    assert ActionItem.objects.count() == 0


def test_action_item_not_found(auth_client):
    import uuid
    url = '/api/v1/action-items/%s/' % uuid.uuid4()
    res = auth_client.delete(url)
    assert res.status_code == 404


# ── Cross-Meeting List ───────────────────────────────────────────

def test_cross_meeting_list(auth_client, meeting, action_item):
    m2 = Meeting.objects.create(title='Another meeting', created_by=action_item.created_by)
    ActionItem.objects.create(meeting=m2, created_by=action_item.created_by, text='Task 2')

    url = '/api/v1/action-items/'
    res = auth_client.get(url)
    assert res.status_code == 200
    assert res.json()['data']['pagination']['total'] == 2


def test_cross_meeting_filter_by_status(auth_client, meeting, action_item):
    action_item.status = 'done'
    action_item.save()
    ActionItem.objects.create(meeting=meeting, created_by=action_item.created_by, text='Open Task', status='open')

    url = '/api/v1/action-items/?status=done'
    res = auth_client.get(url)
    assert res.status_code == 200
    assert res.json()['data']['pagination']['total'] == 1
    assert res.json()['data']['results'][0]['status'] == 'done'


# ── Task Tests ───────────────────────────────────────────────────

def test_populate_action_items_from_summary(db, meeting, user):
    from transcripts.models import MeetingSummary
    from action_items.tasks import populate_action_items_from_summary

    summary = MeetingSummary.objects.create(
        meeting=meeting,
        created_by=user,
        action_items=["Task A", "Task B"]
    )

    populate_action_items_from_summary(summary.id)

    assert ActionItem.objects.count() == 2
    assert ActionItem.objects.filter(text="Task A").exists()
    assert ActionItem.objects.filter(meeting=meeting).count() == 2


def test_populate_skips_empty_summary(db, meeting, user):
    from transcripts.models import MeetingSummary
    from action_items.tasks import populate_action_items_from_summary

    summary = MeetingSummary.objects.create(
        meeting=meeting,
        created_by=user,
        action_items=[]
    )

    populate_action_items_from_summary(summary.id)
    assert ActionItem.objects.count() == 0
