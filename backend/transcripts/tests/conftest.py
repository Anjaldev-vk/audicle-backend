import pytest
from meetings.models import Meeting
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


@pytest.fixture
def user(individual_user):
    return individual_user


@pytest.fixture
def create_user(db):
    def _create_user(email='other@example.com'):
        User = get_user_model()
        return User.objects.create_user(
            email=email,
            password='Password123!',
            first_name='Other',
            last_name='User',
        )
    return _create_user


@pytest.fixture
def meeting(db, user):
    """Create a personal meeting (no organisation) for testing."""
    return Meeting.objects.create(
        title="Test Meeting",
        platform=Meeting.Platform.UPLOAD,
        created_by=user,
        organisation=None,
        status=Meeting.Status.COMPLETED,
    )
