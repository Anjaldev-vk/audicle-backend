import pytest
from django.urls import reverse
from django.test import override_settings
from rest_framework.test import APIClient

@pytest.fixture
def client():
    return APIClient()


@pytest.mark.django_db
class TestRegisterThrottle:

    def test_register_blocks_on_6th_request(self, client):
        url = reverse("register", kwargs={"version": "v1"})
        payload = {
            "email": "test@example.com",
            "password": "StrongPass123!",
            "first_name": "Test",
            "last_name": "User",
            "account_type": "individual",
        }
        for i in range(5):
            client.post(url, payload, format="json")

        response = client.post(url, payload, format="json")
        assert response.status_code == 429

    def test_register_429_matches_standard_format(self, client):
        url = reverse("register", kwargs={"version": "v1"})
        payload = {
            "email": "test@example.com",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
            "first_name": "Test",
            "last_name": "User",
            "account_type": "individual",
        }
        for _ in range(5):
            client.post(url, payload, format="json")

        response = client.post(url, payload, format="json")
        assert response.status_code == 429
        data = response.json()
        assert data["status"] == "error"
        assert data["code"] == "throttled"
        assert "message" in data
        assert "errors" in data
        assert "Retry-After" in response


@pytest.mark.django_db
class TestLoginThrottle:

    def test_login_blocks_on_6th_request(self, client):
        url = reverse("login", kwargs={"version": "v1"})
        payload = {"email": "any@example.com", "password": "wrong"}
        for _ in range(5):
            client.post(url, payload, format="json")

        response = client.post(url, payload, format="json")
        assert response.status_code == 429

    def test_login_429_has_retry_after_header(self, client):
        url = reverse("login", kwargs={"version": "v1"})
        payload = {"email": "any@example.com", "password": "wrong"}
        for _ in range(5):
            client.post(url, payload, format="json")

        response = client.post(url, payload, format="json")
        assert "Retry-After" in response


@pytest.mark.django_db
class TestPasswordResetThrottle:

    def test_password_reset_blocks_on_6th_request(self, client):
        url = reverse("password_reset_request", kwargs={"version": "v1"})
        payload = {"email": "any@example.com"}
        for _ in range(5):
            client.post(url, payload, format="json")

        response = client.post(url, payload, format="json")
        assert response.status_code == 429


@pytest.mark.django_db
class TestThrottleIsolation:

    def test_different_ips_have_separate_counters(self, client):
        """Two different IPs should not share the same throttle counter."""
        url = reverse("login", kwargs={"version": "v1"})
        payload = {"email": "any@example.com", "password": "wrong"}

        # Exhaust limit for IP 1
        for _ in range(5):
            client.post(
                url, payload, format="json",
                REMOTE_ADDR="1.2.3.4"
            )
        blocked = client.post(
            url, payload, format="json",
            REMOTE_ADDR="1.2.3.4"
        )
        assert blocked.status_code == 429

        # IP 2 should still be allowed
        allowed = client.post(
            url, payload, format="json",
            REMOTE_ADDR="5.6.7.8"
        )
        assert allowed.status_code != 429
