import pytest
from rest_framework.test import APIClient
from accounts.models import User
from meetings.models import MeetingTemplate


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='test@example.com', password='testpass123', is_verified=True,
        first_name='Test', last_name='User'
    )


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_create_template(auth_client):
    res = auth_client.post(
        '/api/v1/meetings/templates/',
        {
            'name': 'Weekly standup',
            'description': 'Weekly team sync',
            'default_participants': [],
            'summary_format': 'Format string',
        },
        format='json',
    )
    assert res.status_code == 201
    assert res.json()['data']['name'] == 'Weekly standup'


def test_list_templates(auth_client, user):
    MeetingTemplate.objects.create(
        name='Sprint planning',
        created_by=user,
    )
    res = auth_client.get('/api/v1/meetings/templates/')
    assert res.status_code == 200
    assert len(res.json()['data']) == 1


def test_delete_template(auth_client, user):
    template = MeetingTemplate.objects.create(
        name='One-on-one', created_by=user
    )
    res = auth_client.delete('/api/v1/meetings/templates/%s/' % template.id)
    assert res.status_code == 200
    assert not MeetingTemplate.objects.filter(id=template.id).exists()


def test_delete_template_not_owned(auth_client, db):
    other = User.objects.create_user(
        email='other@example.com', password='pass', is_verified=True,
        first_name='Other', last_name='User'
    )
    template = MeetingTemplate.objects.create(
        name='Other template', created_by=other
    )
    res = auth_client.delete('/api/v1/meetings/templates/%s/' % template.id)
    assert res.status_code == 404
