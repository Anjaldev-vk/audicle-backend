# accounts/tests/test_login.py
import pytest

LOGIN_URL = '/api/v1/accounts/login/'


@pytest.mark.django_db
class TestLogin:

    def test_login_success(self, api_client, individual_user):
        response = api_client.post(LOGIN_URL, {
            'email':    'individual@test.com',
            'password': 'Password123!',
        }, format='json')
        assert response.status_code == 200
        assert 'user' in response.data

    def test_login_wrong_password(self, api_client, individual_user):
        response = api_client.post(LOGIN_URL, {
            'email':    'individual@test.com',
            'password': 'WRONGPASSWORD',
        }, format='json')
        assert response.status_code == 400
        assert 'email' in response.data['errors']

    def test_login_wrong_email(self, api_client):
        response = api_client.post(LOGIN_URL, {
            'email':    'doesnotexist@test.com',
            'password': 'Password123!',
        }, format='json')
        assert response.status_code == 400

    def test_login_deactivated_account(self, api_client, deactivated_user):
        response = api_client.post(LOGIN_URL, {
            'email':    'inactive@test.com',
            'password': 'Password123!',
        }, format='json')
        assert response.status_code == 400
        assert 'deactivated' in str(response.data).lower()

    def test_login_missing_fields(self, api_client):
        response = api_client.post(LOGIN_URL, {}, format='json')
        assert response.status_code == 400

    def test_login_google_user_with_password(self, api_client, db):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(
            email='googleuser@test.com',
            password=None,
            first_name='Google',
            last_name='User',
        )
        user.set_unusable_password()
        user.save()

        response = api_client.post(LOGIN_URL, {
            'email':    'googleuser@test.com',
            'password': 'anypassword',
        }, format='json')
        assert response.status_code == 400
        assert 'Social Login' in str(response.data) or 'Google' in str(response.data)

@pytest.mark.django_db
class TestLoginEdgeCases:
    def test_login_empty_email(self, api_client):
        response = api_client.post(LOGIN_URL, {
            'email':    '',
            'password': 'Password123!',
        }, format='json')
        assert response.status_code == 400

    def test_login_invalid_email_format(self, api_client):
        response = api_client.post(LOGIN_URL, {
            'email':    'notanemail',
            'password': 'Password123!',
        }, format='json')
        assert response.status_code == 400