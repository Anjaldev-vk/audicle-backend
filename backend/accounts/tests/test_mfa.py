import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.mfa_utils import (
    check_recovery_rate_limit,
    generate_email_otp,
    generate_mfa_secret,
    generate_mfa_token,
    store_email_otp,
    verify_app_code,
    verify_email_otp,
    verify_mfa_token,
)

THROTTLE_SETTINGS = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache"
    }
}


@pytest.fixture(autouse=True)
def mfa_test_settings(settings):
    """Override settings for MFA tests."""
    settings.CACHES = THROTTLE_SETTINGS
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user(db):
    from accounts.models import User
    return User.objects.create_user(
        email="mfa@example.com",
        password="StrongPass123!",
        first_name="MFA",
        last_name="User",
    )


@pytest.fixture
def mfa_user(db):
    """A user with MFA already enabled."""
    from accounts.models import User
    secret = generate_mfa_secret()
    u = User.objects.create_user(
        email="mfaon@example.com",
        password="StrongPass123!",
        first_name="MFA",
        last_name="On",
    )
    u.mfa_secret  = secret
    u.mfa_enabled = True
    u.save()
    return u


@pytest.fixture
def auth_client(client, user):
    client.force_authenticate(user=user)
    return client


# ── mfa_utils unit tests ──────────────────────────────────────────────────────

class TestMFAUtils:

    def test_generate_mfa_secret_length(self):
        assert len(generate_mfa_secret()) == 32

    def test_verify_app_code_valid(self):
        import pyotp
        secret = generate_mfa_secret()
        code   = pyotp.TOTP(secret).now()
        assert verify_app_code(secret, code) is True

    def test_verify_app_code_invalid(self):
        assert verify_app_code(generate_mfa_secret(), "000000") is False

    def test_generate_email_otp_is_6_digits(self):
        otp = generate_email_otp()
        assert len(otp) == 6
        assert otp.isdigit()

    def test_mfa_token_roundtrip(self):
        token = generate_mfa_token("test-uuid")
        assert verify_mfa_token(token) == "test-uuid"

    def test_mfa_token_tampered_returns_none(self):
        assert verify_mfa_token("bad.token.here") is None

    def test_email_otp_single_use(self):
        store_email_otp("user-1", "123456")
        assert verify_email_otp("user-1", "123456") is True
        # Second use must fail
        assert verify_email_otp("user-1", "123456") is False

    def test_email_otp_wrong_code(self):
        store_email_otp("user-2", "123456")
        assert verify_email_otp("user-2", "999999") is False

    def test_recovery_rate_limit_allows_3(self):
        for _ in range(3):
            assert check_recovery_rate_limit("user-3") is True

    def test_recovery_rate_limit_blocks_4th(self):
        for _ in range(3):
            check_recovery_rate_limit("user-4")
        assert check_recovery_rate_limit("user-4") is False


# ── Enable MFA ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEnableMFA:

    def test_enable_returns_totp_uri(self, auth_client):
        response = auth_client.post(reverse("mfa-enable", kwargs={"version": "v1"}))
        assert response.status_code == 200
        assert "totp_uri" in response.json()

    def test_enable_stores_secret(self, auth_client, user):
        auth_client.post(reverse("mfa-enable", kwargs={"version": "v1"}))
        user.refresh_from_db()
        assert user.mfa_secret is not None

    def test_enable_twice_returns_400(self, auth_client, user):
        user.mfa_enabled = True
        user.save()
        response = auth_client.post(reverse("mfa-enable", kwargs={"version": "v1"}))
        assert response.status_code == 400
        assert response.json()["code"] == "mfa_already_enabled"

    def test_enable_requires_auth(self, client):
        response = client.post(reverse("mfa-enable", kwargs={"version": "v1"}))
        assert response.status_code == 401


# ── Verify MFA Setup ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVerifyMFASetup:

    def test_valid_code_activates_mfa(self, auth_client, user):
        import pyotp
        secret = generate_mfa_secret()
        user.mfa_secret = secret
        user.save()

        code     = pyotp.TOTP(secret).now()
        response = auth_client.post(
            reverse("mfa-verify-setup", kwargs={"version": "v1"}),
            {"totp_code": code},
            format="json",
        )
        assert response.status_code == 200
        user.refresh_from_db()
        assert user.mfa_enabled is True

    def test_invalid_code_returns_400(self, auth_client, user):
        user.mfa_secret = generate_mfa_secret()
        user.save()
        response = auth_client.post(
            reverse("mfa-verify-setup", kwargs={"version": "v1"}),
            {"totp_code": "000000"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_totp_code"

    def test_no_secret_returns_400(self, auth_client):
        response = auth_client.post(
            reverse("mfa-verify-setup", kwargs={"version": "v1"}),
            {"totp_code": "123456"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "mfa_setup_not_initiated"


# ── Primary MFA login path ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMFATokenVerify:

    def test_valid_token_and_code_returns_jwt(self, client, mfa_user):
        import pyotp
        mfa_token = generate_mfa_token(str(mfa_user.id))
        code      = pyotp.TOTP(mfa_user.mfa_secret).now()
        response  = client.post(
            reverse("mfa-verify", kwargs={"version": "v1"}),
            {"mfa_token": mfa_token, "totp_code": code},
            format="json",
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    def test_invalid_mfa_token_returns_401(self, client):
        response = client.post(
            reverse("mfa-verify", kwargs={"version": "v1"}),
            {"mfa_token": "bad.token", "totp_code": "123456"},
            format="json",
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_mfa_token"

    def test_wrong_totp_code_returns_401(self, client, mfa_user):
        mfa_token = generate_mfa_token(str(mfa_user.id))
        response  = client.post(
            reverse("mfa-verify", kwargs={"version": "v1"}),
            {"mfa_token": mfa_token, "totp_code": "000000"},
            format="json",
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_totp_code"


# ── Fallback recovery path ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMFARecovery:

    def test_recovery_request_queues_email(self, client, mfa_user):
        mfa_token = generate_mfa_token(str(mfa_user.id))
        response  = client.post(
            reverse("mfa-recover-request", kwargs={"version": "v1"}),
            {"mfa_token": mfa_token},
            format="json",
        )
        assert response.status_code == 200

    def test_recovery_request_rate_limited_after_3(self, client, mfa_user):
        for _ in range(3):
            check_recovery_rate_limit(str(mfa_user.id))

        mfa_token = generate_mfa_token(str(mfa_user.id))
        response  = client.post(
            reverse("mfa-recover-request", kwargs={"version": "v1"}),
            {"mfa_token": mfa_token},
            format="json",
        )
        assert response.status_code == 429
        assert response.json()["code"] == "recovery_rate_limited"

    def test_recovery_verify_issues_jwt_and_disables_mfa(self, client, mfa_user):
        otp = generate_email_otp()
        store_email_otp(str(mfa_user.id), otp)

        mfa_token = generate_mfa_token(str(mfa_user.id))
        response  = client.post(
            reverse("mfa-recover-verify", kwargs={"version": "v1"}),
            {"mfa_token": mfa_token, "email_code": otp},
            format="json",
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

        mfa_user.refresh_from_db()
        assert mfa_user.mfa_enabled is False
        assert mfa_user.mfa_secret is None

    def test_recovery_verify_wrong_code_returns_401(self, client, mfa_user):
        store_email_otp(str(mfa_user.id), "123456")
        mfa_token = generate_mfa_token(str(mfa_user.id))
        response  = client.post(
            reverse("mfa-recover-verify", kwargs={"version": "v1"}),
            {"mfa_token": mfa_token, "email_code": "999999"},
            format="json",
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_email_code"

    def test_recovery_verify_invalid_mfa_token_returns_401(self, client):
        response = client.post(
            reverse("mfa-recover-verify", kwargs={"version": "v1"}),
            {"mfa_token": "bad.token", "email_code": "123456"},
            format="json",
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_mfa_token"


# ── Disable MFA ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDisableMFA:

    def test_disable_with_valid_totp(self, client, mfa_user):
        import pyotp
        client.force_authenticate(user=mfa_user)
        code     = pyotp.TOTP(mfa_user.mfa_secret).now()
        response = client.post(
            reverse("mfa-disable", kwargs={"version": "v1"}),
            {"totp_code": code},
            format="json",
        )
        assert response.status_code == 200
        mfa_user.refresh_from_db()
        assert mfa_user.mfa_enabled is False
        assert mfa_user.mfa_secret is None

    def test_disable_invalid_code_returns_400(self, client, mfa_user):
        client.force_authenticate(user=mfa_user)
        response = client.post(
            reverse("mfa-disable", kwargs={"version": "v1"}),
            {"totp_code": "000000"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_totp_code"

    def test_disable_when_not_enabled_returns_400(self, auth_client):
        response = auth_client.post(
            reverse("mfa-disable", kwargs={"version": "v1"}),
            {"totp_code": "123456"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "mfa_not_enabled"

    def test_disable_requires_auth(self, client):
        response = client.post(reverse("mfa-disable", kwargs={"version": "v1"}))
        assert response.status_code == 401


# ── Login intercept ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLoginMFAIntercept:

    def test_login_with_mfa_enabled_returns_mfa_token(self, client, mfa_user):
        response = client.post(
            reverse("login", kwargs={"version": "v1"}),
            {"email": mfa_user.email, "password": "StrongPass123!"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mfa_required"] is True
        assert "mfa_token" in data

    def test_login_without_mfa_returns_jwt_directly(self, client, user):
        response = client.post(
            reverse("login", kwargs={"version": "v1"}),
            {"email": user.email, "password": "StrongPass123!"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert "mfa_required" not in data
        assert "access_token" in data
