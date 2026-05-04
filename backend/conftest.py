import pytest
from django.db import connection
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch, MagicMock


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
            cursor.execute('CREATE EXTENSION IF NOT EXISTS vector;')


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
def org_admin_client(org_admin, organisation):
    """Authenticated client for org admin with workspace context."""
    from rest_framework_simplejwt.tokens import RefreshToken
    client = APIClient()
    refresh = RefreshToken.for_user(org_admin)
    client.credentials(
        HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}',
        HTTP_X_WORKSPACE_ID=str(organisation.id)
    )
    return client


@pytest.fixture
def org_member_client(org_member, organisation):
    """Authenticated client for org member with workspace context."""
    from rest_framework_simplejwt.tokens import RefreshToken
    client = APIClient()
    refresh = RefreshToken.for_user(org_member)
    client.credentials(
        HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}',
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
def org_admin(db, organisation):
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
def org_member(db, organisation):
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