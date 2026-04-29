# backend/conftest.py
import pytest
from django.db import connection
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta


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
    # Use InMemoryChannelLayer so WebSocket tests don't hang waiting for Redis
    settings.CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }


@pytest.fixture(autouse=True)
def clear_test_cache():
    """Clear the cache before every test to prevent throttle state leakage."""
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


# ── API Clients ───────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def auth_client(api_client, individual_user):
    """Authenticated client for individual user."""
    api_client.force_authenticate(user=individual_user)
    return api_client


@pytest.fixture
def org_admin_client(api_client, org_admin):
    """Authenticated client for org admin."""
    api_client.force_authenticate(user=org_admin)
    return api_client


@pytest.fixture
def org_member_client(api_client, org_member):
    """Authenticated client for org member."""
    api_client.force_authenticate(user=org_member)
    return api_client


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
    User = get_user_model()
    return User.objects.create_user(
        email='admin@testorg.com',
        password='Password123!',
        first_name='Admin',
        last_name='User',
        organisation=organisation,
        org_role='owner',
    )


@pytest.fixture
def org_member(db, organisation):
    User = get_user_model()
    return User.objects.create_user(
        email='member@testorg.com',
        password='Password123!',
        first_name='Member',
        last_name='User',
        organisation=organisation,
        org_role='member',
    )


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