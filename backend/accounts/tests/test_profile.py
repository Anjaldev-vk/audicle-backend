# accounts/tests/test_profile.py
import pytest

ME_URL = '/api/v1/accounts/me/'


@pytest.mark.django_db
class TestProfile:

    def test_get_profile_authenticated(self, auth_client, individual_user):
        response = auth_client.get(ME_URL)
        assert response.status_code == 200
        assert response.data['email'] == 'individual@test.com'
        assert response.data['account_type'] == 'individual'

    def test_get_profile_unauthenticated(self, api_client):
        response = api_client.get(ME_URL)
        assert response.status_code == 401

    def test_update_profile(self, auth_client):
        response = auth_client.patch(ME_URL, {
            'job_title':    'Senior Engineer',
            'phone_number': '1234567890',
        }, format='json')
        assert response.status_code == 200
        assert response.data['job_title'] == 'Senior Engineer'
        assert response.data['phone_number'] == '1234567890'

    def test_cannot_update_email(self, auth_client):
        response = auth_client.patch(ME_URL, {
            'email': 'newemail@test.com',
        }, format='json')
        assert response.status_code == 200
        # email should not change
        assert response.data['email'] == 'individual@test.com'


@pytest.mark.django_db
class TestChangePassword:

    def test_change_password_success(self, auth_client):
        response = auth_client.post('/api/v1/accounts/change-password/', {
            'old_password':     'Password123!',
            'new_password':     'NewPassword456!',
            'confirm_password': 'NewPassword456!',
        }, format='json')
        assert response.status_code == 200

    def test_change_password_wrong_old(self, auth_client):
        response = auth_client.post('/api/v1/accounts/change-password/', {
            'old_password':     'WRONGPASSWORD',
            'new_password':     'NewPassword456!',
            'confirm_password': 'NewPassword456!',
        }, format='json')
        assert response.status_code == 400
        assert 'old_password' in response.data['errors']

    def test_change_password_mismatch(self, auth_client):
        response = auth_client.post('/api/v1/accounts/change-password/', {
            'old_password':     'Password123!',
            'new_password':     'NewPassword456!',
            'confirm_password': 'DIFFERENT',
        }, format='json')
        assert response.status_code == 400

@pytest.mark.django_db
class TestProfileEdgeCases:
    def test_update_profile_unauthenticated(self, api_client):
        response = api_client.patch(ME_URL, {
            'job_title': 'Hacker',
        }, format='json')
        assert response.status_code == 401

    def test_change_password_unauthenticated(self, api_client):
        response = api_client.post('/api/v1/accounts/change-password/', {
            'old_password':     'Password123!',
            'new_password':     'NewPassword456!',
            'confirm_password': 'NewPassword456!',
        }, format='json')
        assert response.status_code == 401

    # def test_change_password_same_as_old(self, auth_client):
    #     response = auth_client.post('/api/v1/accounts/change-password/', {
    #         'old_password':     'Password123!',
    #         'new_password':     'Password123!',
    #         'confirm_password': 'Password123!',
    #     }, format='json')
    #     assert response.status_code == 400

    def test_change_password_weak_new(self, auth_client):
        response = auth_client.post('/api/v1/accounts/change-password/', {
            'old_password':     'Password123!',
            'new_password':     '123',
            'confirm_password': '123',
        }, format='json')
        assert response.status_code == 400