import pytest
from django.urls import reverse
from rest_framework import status


REGISTER_URL = '/api/v1/accounts/register/'

@pytest.mark.django_db
class TestRegisterIndividual:
    """
    Tests the registration of individual users (B2C path).
    Verifies that no organisation is created and security tokens are issued correctly.
    """
    def test_register_individual_success(self, api_client):
        url = reverse('register', kwargs={'version': 'v1'})
        data = {
            "email": "newuser@test.com",
            "password": "StrongPassword123!",
            "confirm_password": "StrongPassword123!",
            "first_name": "New",
            "last_name": "User",
            "account_type": "individual"
        }
        response = api_client.post(url, data, format='json')
        
        assert response.status_code == status.HTTP_201_CREATED
        
        assert 'tokens' in response.data['data']
        assert 'access' in response.data['data']['tokens']
        
        assert 'refresh' not in response.data['data']['tokens']
        
        assert 'refresh_token' in response.cookies
        assert response.cookies['refresh_token']['httponly'] is True
        
        assert 'user' in response.data['data']
        assert response.data['data']['user']['email'] == "newuser@test.com"
        assert response.data['data']['user']['organisation'] is None

    def test_register_duplicate_email(self, api_client, individual_user):
        payload = {
            'email': 'individual@test.com',
            'password': 'Password123!',
            'confirm_password': 'Password123!',
            'first_name': 'Another',
            'last_name': 'User',
            'account_type': 'individual',
        }
        response = api_client.post(REGISTER_URL, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'email' in response.data['errors']

    def test_register_password_mismatch(self, api_client):
        payload = {
            'email': 'mismatch@test.com',
            'password': 'Password123!',
            'confirm_password': 'DIFFERENT_PASSWORD',
            'first_name': 'Test',
            'last_name': 'User',
            'account_type': 'individual',
        }
        response = api_client.post(REGISTER_URL, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'non_field_errors' in response.data['errors'] or 'confirm_password' in response.data['errors']

@pytest.mark.django_db
class TestRegisterOrganisation:
    """
    Tests the B2B registration path where a user creates a new organisation.
    """
    def test_register_create_org_success(self, api_client):
        payload = {
            'email': 'orgadmin@test.com',
            'password': 'Password123!',
            'confirm_password': 'Password123!',
            'first_name': 'Org',
            'last_name': 'Admin',
            'account_type': 'create_org',
            'org_name': 'My Company',
            'org_slug': 'my-company',
        }
        response = api_client.post(REGISTER_URL, payload, format='json')
        
        assert response.status_code == status.HTTP_201_CREATED
        
        assert response.data['data']['user']['org_role'] == 'owner'
        assert response.data['data']['user']['organisation'] is not None
        
        assert 'tokens' in response.data['data']
        assert 'refresh_token' in response.cookies

@pytest.mark.django_db
class TestRegisterViaInvite:
    """
    Tests joining an existing organisation via a generated invite code.
    """
    def test_register_join_org_success(self, api_client, valid_invite):
        payload = {
            'email': 'invited@test.com',
            'password': 'Password123!',
            'confirm_password': 'Password123!',
            'first_name': 'Invited',
            'last_name': 'User',
            'account_type': 'join_org',
            'invite_code': valid_invite.code,
        }
        response = api_client.post(REGISTER_URL, payload, format='json')
        
        assert response.status_code == status.HTTP_201_CREATED
        
        assert response.data['data']['user']['org_role'] == 'member'
        assert response.data['data']['user']['organisation'] is not None
        
        from accounts.models import OrganisationInvite
        invite = OrganisationInvite.objects.get(code=valid_invite.code)
        assert invite.status == OrganisationInvite.Status.ACCEPTED
    
    @pytest.mark.django_db
    class TestRegisterEdgeCases:
        """
        Tests edge cases and error scenarios for user registration.
        """
        def test_register_weak_password(self, api_client):
            payload = {
                'email': 'weak@test.com',
                'password': '123',
                'confirm_password': '123',
                'first_name': 'Test',
                'last_name': 'User',
                'account_type': 'individual',
            }
            response = api_client.post(REGISTER_URL, payload, format='json')
            assert response.status_code == 400

        def test_register_missing_required_fields(self, api_client):
            response = api_client.post(REGISTER_URL, {}, format='json')
            assert response.status_code == 400

        def test_register_create_org_missing_name(self, api_client):
            payload = {
                'email': 'noname@test.com',
                'password': 'Password123!',
                'confirm_password': 'Password123!',
                'first_name': 'No',
                'last_name': 'Name',
                'account_type': 'create_org',
            }
            response = api_client.post(REGISTER_URL, payload, format='json')
            assert response.status_code == 400

        def test_register_join_org_wrong_email(self, api_client, valid_invite):
            payload = {
                'email': 'wrong@test.com',
                'password': 'Password123!',
                'confirm_password': 'Password123!',
                'first_name': 'Wrong',
                'last_name': 'Email',
                'account_type': 'join_org',
                'invite_code': valid_invite.code,
            }
            response = api_client.post(REGISTER_URL, payload, format='json')
            assert response.status_code == 400
            assert 'invite_code' in response.data['errors']

        def test_register_join_org_expired_invite(self, api_client, expired_invite):
            payload = {
                'email': 'expired@test.com',
                'password': 'Password123!',
                'confirm_password': 'Password123!',
                'first_name': 'Expired',
                'last_name': 'Invite',
                'account_type': 'join_org',
                'invite_code': expired_invite.code,
            }
            response = api_client.post(REGISTER_URL, payload, format='json')
            assert response.status_code == 400

        def test_register_join_org_invalid_code(self, api_client):
            payload = {
                'email': 'someone@test.com',
                'password': 'Password123!',
                'confirm_password': 'Password123!',
                'first_name': 'Some',
                'last_name': 'One',
                'account_type': 'join_org',
                'invite_code': 'completely-fake-code',
            }
            response = api_client.post(REGISTER_URL, payload, format='json')
            assert response.status_code == 400

    