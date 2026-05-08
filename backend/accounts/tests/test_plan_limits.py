import pytest
from rest_framework.test import APIClient
from accounts.models import User, Organisation, Membership
from meetings.models import Meeting
from unittest.mock import patch


@pytest.fixture
def free_user(db):
    return User.objects.create_user(
        email='free@example.com',
        password='testpass123',
        is_verified=True,
        plan='free',
        first_name='Free',
        last_name='User',
    )


@pytest.fixture
def pro_user(db):
    return User.objects.create_user(
        email='pro@example.com',
        password='testpass123',
        is_verified=True,
        plan='pro',
        first_name='Pro',
        last_name='User',
    )


@pytest.fixture
def auth_client(free_user):
    client = APIClient()
    client.force_authenticate(user=free_user)
    return client


@pytest.fixture
def pro_client(pro_user):
    client = APIClient()
    client.force_authenticate(user=pro_user)
    return client


# ── Workspace limits ─────────────────────────────────────────────

def test_free_user_can_create_two_workspaces(auth_client, free_user, db):
    # User can create 2 workspaces in Free plan
    for i in range(2):
        res = auth_client.post(
            '/api/v1/accounts/workspaces/create/',
            {'name': 'Org %s' % i, 'slug': 'org-%s' % i},
            format='json',
        )
        assert res.status_code in [200, 201]


def test_free_user_cannot_create_third_workspace(
    auth_client, free_user, db
):
    # Create 2 orgs and make user owner
    for i in range(2):
        org = Organisation.objects.create(name='Org %s' % i, slug='org-%s' % i)
        Membership.objects.create(
            user=free_user, organisation=org, role='owner'
        )

    res = auth_client.post(
        '/api/v1/accounts/workspaces/create/',
        {'name': 'Third Org', 'slug': 'third-org'},
        format='json',
    )
    assert res.status_code == 403
    assert res.json()['code'] == 'plan_limit_reached'


def test_pro_user_can_create_four_workspaces(pro_client, pro_user, db):
    # Already has 3 orgs
    for i in range(3):
        org = Organisation.objects.create(
            name='Org %s' % i, slug='org-%s' % i
        )
        Membership.objects.create(
            user=pro_user, organisation=org, role='owner'
        )

    res = pro_client.post(
        '/api/v1/accounts/workspaces/create/',
        {'name': 'Fourth Org', 'slug': 'fourth-org'},
        format='json',
    )
    assert res.status_code in [200, 201]


def test_pro_user_cannot_create_fifth_workspace(
    pro_client, pro_user, db
):
    for i in range(4):
        org = Organisation.objects.create(
            name='Org %s' % i, slug='org-%s' % i
        )
        Membership.objects.create(
            user=pro_user, organisation=org, role='owner'
        )

    res = pro_client.post(
        '/api/v1/accounts/workspaces/create/',
        {'name': 'Fifth Org', 'slug': 'fifth-org'},
        format='json',
    )
    assert res.status_code == 403


# ── Member limits ────────────────────────────────────────────────

def test_free_org_cannot_exceed_member_limit(auth_client, free_user, db):
    org = Organisation.objects.create(
        name='Free Org', slug='free-org', plan='free'
    )
    Membership.objects.create(
        user=free_user, organisation=org, role='owner'
    )
    # Add 2 more members to hit limit of 3
    for i in range(2):
        u = User.objects.create_user(
            email='member%s@example.com' % i,
            password='pass',
            is_verified=True,
            first_name='Member',
            last_name=str(i),
        )
        Membership.objects.create(user=u, organisation=org, role='member')

    auth_client.defaults['HTTP_X_WORKSPACE_ID'] = str(org.id)
    res = auth_client.post(
        '/api/v1/accounts/organisation/invite/',
        {'email': 'newmember@example.com', 'role': 'member'},
        format='json',
    )
    assert res.status_code == 403
    assert res.json()['code'] == 'plan_limit_reached'


# ── Bot access ───────────────────────────────────────────────────

def test_free_user_CAN_dispatch_bot(auth_client, free_user, db):
    # Free plan now has bot_access=True
    meeting = Meeting.objects.create(
        title='Test meeting',
        platform='zoom',
        created_by=free_user,
        status='scheduled',
        meeting_url='https://zoom.us/j/123',
    )
    with patch('utils.kafka_producer.send_bot_task') as mock_bot:
        res = auth_client.post(
            '/api/v1/meetings/%s/bot/dispatch/' % meeting.id
        )
        assert res.status_code != 403


# ── RAG access ───────────────────────────────────────────────────

def test_free_user_CAN_create_chat_session(auth_client):
    # Free plan now has rag_access=True
    res = auth_client.post(
        '/api/v1/rag/chat/sessions/',
        {'title': 'My chat'},
        format='json',
    )
    assert res.status_code != 403


# ── Meeting limits ───────────────────────────────────────────────

def test_free_user_cannot_exceed_meeting_limit(auth_client, free_user):
    free_user.meetings_this_month = 5
    free_user.save()

    res = auth_client.post(
        '/api/v1/meetings/',
        {
            'title': 'One more meeting',
            'platform': 'zoom',
            'meeting_url': 'https://zoom.us/j/999',
        },
        format='json',
    )
    assert res.status_code == 403
    assert res.json()['code'] == 'plan_limit_reached'


def test_free_user_within_meeting_limit_can_create(
    auth_client, free_user
):
    free_user.meetings_this_month = 3
    free_user.save()

    res = auth_client.post(
        '/api/v1/meetings/',
        {'title': 'New meeting', 'platform': 'upload'},
        format='json',
    )
    assert res.status_code in [200, 201]


def test_enterprise_user_has_no_meeting_limit(db):
    enterprise_user = User.objects.create_user(
        email='enterprise@example.com',
        password='pass',
        is_verified=True,
        plan='enterprise',
        first_name='Enterprise',
        last_name='User',
    )
    enterprise_user.meetings_this_month = 999
    enterprise_user.save()

    client = APIClient()
    client.force_authenticate(user=enterprise_user)

    res = client.post(
        '/api/v1/meetings/',
        {'title': 'New meeting', 'platform': 'upload'},
        format='json',
    )
    assert res.status_code != 403


# ── Helper unit tests ────────────────────────────────────────────

def test_check_limit_returns_false_for_none():
    from accounts.utils import check_limit
    assert check_limit(999, None) is False


def test_check_limit_returns_true_when_reached():
    from accounts.utils import check_limit
    assert check_limit(5, 5) is True
    assert check_limit(6, 5) is True


def test_check_limit_returns_false_when_under():
    from accounts.utils import check_limit
    assert check_limit(4, 5) is False


def test_get_plan_limits_defaults_to_free():
    from accounts.utils import get_plan_limits
    limits = get_plan_limits('unknown_plan')
    assert limits['meetings_per_month'] == 5
