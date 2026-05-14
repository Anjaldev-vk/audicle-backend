import pytest
import os
from django.db import connection
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch, MagicMock

# Force local host for external services during tests running on Windows
os.environ.setdefault('DB_HOST', 'localhost')
os.environ.setdefault('DB_PORT', '5432')
os.environ.setdefault('REDIS_HOST', 'localhost')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('REDIS_CACHE_URL', 'redis://localhost:6379/1')
os.environ.setdefault('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2')

# Mock pgvector VectorField for local tests without the extension
import pgvector.django
from django.db import models

class MockVectorField(models.Field):
    def __init__(self, dimensions=None, *args, **kwargs):
        self.dimensions = dimensions
        super().__init__(*args, **kwargs)
    def db_type(self, connection):
        return 'float8[]'
    def from_db_value(self, value, expression, connection):
        return value
    def to_python(self, value):
        return value
    def get_prep_value(self, value):
        return value

pgvector.django.VectorField = MockVectorField


def get_results(response):
    """Extract results list from paginated or non-paginated response."""
    data = response.json().get('data', {})
    if 'results' in data:
        return data['results']
    return data

@pytest.fixture(autouse=True)
def mock_kafka(settings):
    """
    Auto-applied to every test.
    Prevents real Kafka connections during testing.
    """
    with patch('utils.kafka_producer.get_producer') as mock_get:
        mock_p = MagicMock()
        mock_get.return_value = mock_p
        yield mock_p


@pytest.fixture(autouse=True)
def mock_celery(settings):
    """
    Auto-applied to every test.
    Prevents real Celery/Redis connections during testing.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    settings.CELERY_BROKER_URL = 'memory://'
    settings.CELERY_RESULT_BACKEND = 'cache+memory://'


@pytest.fixture(autouse=True)
def setup_plans(db):
    """Ensure default plans exist in the database for all tests."""
    from billing.models import Plan
    Plan.objects.get_or_create(
        name='Free',
        defaults={
            'meeting_limit': 5,
            'max_workspaces': 2,
            'max_members': 2,
            'bot_access': False,
            'rag_access': False,
        }
    )
    Plan.objects.get_or_create(
        name='Pro',
        defaults={
            'meeting_limit': 50,
            'max_workspaces': 5,
            'max_members': 10,
            'bot_access': True,
            'rag_access': True,
            'razorpay_plan_id': 'plan_pro_123',
        }
    )


# ── anyio backend — required for @pytest.mark.anyio WebSocket tests ──────────
@pytest.fixture(scope='session')
def anyio_backend():
    return 'asyncio'


# -------------- pgvector: enable extension in test database before any test runs ----------------

@pytest.fixture(scope='session', autouse=True)
def enable_pgvector(django_db_blocker):
    """
    Create the pgvector extension in the test database.
    Must run before Django creates the rag_embeddingchunk table.
    scope='session' ensures it runs once per test session.
    """
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            try:
                cursor.execute('CREATE EXTENSION IF NOT EXISTS vector;')
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Could not create pgvector extension: %s", e)


# ── Global Test Settings ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def test_settings(settings):
    """Ensure each test uses an isolated local memory cache and in-memory channel layer."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache"
        }
    }
    # Set high throttle rates for tests to prevent 429s during test runs.
    # Specific throttle tests will override this.
    settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] = {
        'anon': '1000/min',
        'user': '1000/min',
        'auth': '1000/min',
    }
    
    # Use InMemoryChannelLayer so WebSocket tests don't hang waiting for Redis
    settings.CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }


@pytest.fixture(autouse=True)
def clear_test_cache():
    """Clear all caches before every test to prevent state leakage."""
    from django.core.cache import caches
    for cache_name in caches:
        caches[cache_name].clear()
    yield
    for cache_name in caches:
        caches[cache_name].clear()


# ── API Clients ───────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def auth_client(individual_user):
    """Authenticated client for individual user."""
    client = APIClient()
    client.force_authenticate(user=individual_user)
    return client


@pytest.fixture
def org_admin_client(org_admin, organisation, org_admin_membership, org_subscription):
    """Authenticated client for org admin with workspace context and active subscription."""
    client = APIClient()
    client.force_authenticate(user=org_admin)
    client.credentials(
        HTTP_X_WORKSPACE_ID=str(organisation.id)
    )
    return client


@pytest.fixture
def org_member_client(org_member, organisation, org_member_membership, org_subscription):
    """Authenticated client for org member with workspace context and active subscription."""
    client = APIClient()
    client.force_authenticate(user=org_member)
    client.credentials(
        HTTP_X_WORKSPACE_ID=str(organisation.id)
    )
    return client


# ── User and Organisation Fixtures ────────────────────────────────────────────

@pytest.fixture
def individual_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email='individual@test.com',
        password='Password123!',
        first_name='John',
        last_name='Doe',
    )


@pytest.fixture
def organisation(db):
    from accounts.models import Organisation
    return Organisation.objects.create(
        name='Test Org',
        slug='test-org',
        plan='free',
    )


@pytest.fixture
def org_admin(db, organisation, org_subscription):  # ← add org_subscription
    from accounts.models import Membership
    User = get_user_model()
    user = User.objects.create_user(
        email='admin@testorg.com',
        password='Password123!',
        first_name='Admin',
        last_name='User',
    )
    Membership.objects.create(
        user=user,
        organisation=organisation,
        role='owner'
    )
    return user


@pytest.fixture
def org_admin_membership(db, org_admin, organisation):
    from accounts.models import Membership
    return Membership.objects.get_or_create(
        user=org_admin,
        organisation=organisation,
        defaults={'role': 'owner'}
    )[0]


@pytest.fixture
def org_member(db, organisation, org_subscription):  # ← add org_subscription
    from accounts.models import Membership
    User = get_user_model()
    user = User.objects.create_user(
        email='member@testorg.com',
        password='Password123!',
        first_name='Member',
        last_name='User',
    )
    Membership.objects.create(
        user=user,
        organisation=organisation,
        role='member'
    )
    return user


@pytest.fixture
def org_member_membership(db, org_member, organisation):
    from accounts.models import Membership
    return Membership.objects.get_or_create(
        user=org_member,
        organisation=organisation,
        defaults={'role': 'member'}
    )[0]


@pytest.fixture
def pro_plan(db):
    from billing.models import Plan
    return Plan.objects.get_or_create(
        name='Pro',
        defaults={
            'price': 20.00,
            'meeting_limit': -1,
            'max_workspaces': -1,
            'max_members': -1,
            'bot_access': True,
            'rag_access': True,
        }
    )[0]


@pytest.fixture
def org_subscription(db, organisation):
    """Give the test organisation a Pro subscription with bot access."""
    from billing.models import Plan, Subscription
    pro_plan = Plan.objects.get(name='Pro')
    try:
        sub = Subscription.objects.get(organisation=organisation)
        sub.plan = pro_plan
        sub.status = 'active'
        sub.save()
    except Subscription.DoesNotExist:
        sub = Subscription.objects.create(
            organisation=organisation,
            plan=pro_plan,
            status='active',
        )
    return sub


@pytest.fixture
def deactivated_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email='inactive@test.com',
        password='Password123!',
        first_name='Inactive',
        last_name='User',
        is_active=False,
    )


# ── Organisation Invite Fixtures ──────────────────────────────────────────────

@pytest.fixture
def valid_invite(db, organisation, org_admin):
    from accounts.models import OrganisationInvite
    return OrganisationInvite.objects.create(
        organisation=organisation,
        invited_by=org_admin,
        email='invited@test.com',
        role='member',
        expires_at=timezone.now() + timedelta(days=7),
    )


@pytest.fixture
def expired_invite(db, organisation, org_admin):
    from accounts.models import OrganisationInvite
    return OrganisationInvite.objects.create(
        organisation=organisation,
        invited_by=org_admin,
        email='expired@test.com',
        role='member',
        expires_at=timezone.now() - timedelta(days=1),
    )


@pytest.fixture
def accepted_invite(db, organisation, org_admin):
    from accounts.models import OrganisationInvite
    return OrganisationInvite.objects.create(
        organisation=organisation,
        invited_by=org_admin,
        email='accepted@test.com',
        role='member',
        status='accepted',
        expires_at=timezone.now() + timedelta(days=7),
    )