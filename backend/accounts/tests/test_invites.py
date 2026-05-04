import pytest
from django.urls import reverse

INVITE_URL = '/api/v1/accounts/organisation/invite/'


@pytest.mark.django_db
class TestCreateInvite:

    def test_admin_can_create_invite(self, org_admin_client):
        response = org_admin_client.post(INVITE_URL, {
            'email': 'newguest@test.com',
            'role':  'member',
        }, format='json')
        assert response.status_code == 201
        assert 'code' in response.data['data']

    def test_member_cannot_create_invite(self, org_member_client):
        response = org_member_client.post(INVITE_URL, {
            'email': 'newguest@test.com',
            'role':  'member',
        }, format='json')
        assert response.status_code == 403

    def test_cannot_invite_existing_member(self, org_admin_client, org_member):
        response = org_admin_client.post(INVITE_URL, {
            'email': 'member@testorg.com',  # already a member
            'role':  'member',
        }, format='json')
        assert response.status_code == 400

    def test_cannot_send_duplicate_invite(self, org_admin_client, valid_invite):
        response = org_admin_client.post(INVITE_URL, {
            'email': 'invited@test.com', 
            'role':  'member',
        }, format='json')
        assert response.status_code == 400

    def test_individual_user_cannot_invite(self, auth_client):
        response = auth_client.post(INVITE_URL, {
            'email': 'someone@test.com',
            'role':  'member',
        }, format='json')
        assert response.status_code == 403


@pytest.mark.django_db
class TestVerifyInvite:

    def test_verify_valid_invite(self, api_client, valid_invite):
        print(f"DEBUG INVITE CODE: '{valid_invite.code}'")
        url = reverse('verify_invite', kwargs={'version': 'v1', 'code': valid_invite.code})
        response = api_client.get(url)
        assert response.status_code == 200
        assert response.data['data']['email'] == 'invited@test.com'
        assert response.data['data']['organisation'] == 'Test Org'

    def test_verify_expired_invite(self, api_client, expired_invite):
        url = reverse('verify_invite', kwargs={'version': 'v1', 'code': expired_invite.code})
        response = api_client.get(url)
        assert response.status_code == 400

    def test_verify_accepted_invite(self, api_client, accepted_invite):
        url = reverse('verify_invite', kwargs={'version': 'v1', 'code': accepted_invite.code})
        response = api_client.get(url)
        assert response.status_code == 400

    def test_verify_fake_code(self, api_client):
        url = reverse('verify_invite', kwargs={'version': 'v1', 'code': 'totally-fake-code'})
        response = api_client.get(url)
        assert response.status_code in [400, 404]
@pytest.mark.django_db
class TestInviteEdgeCases:
    def test_unauthenticated_cannot_create_invite(self, api_client):
        response = api_client.post(INVITE_URL, {
            'email': 'someone@test.com',
            'role':  'member',
        }, format='json')
        assert response.status_code == 401

    def test_invite_invalid_email_format(self, org_admin_client):
        response = org_admin_client.post(INVITE_URL, {
            'email': 'notanemail',
            'role':  'member',
        }, format='json')
        assert response.status_code == 400

    def test_invite_invalid_role(self, org_admin_client):
        response = org_admin_client.post(INVITE_URL, {
            'email': 'valid@test.com',
            'role':  'supervillain',
        }, format='json')
        assert response.status_code == 400

    def test_verify_invite_is_public(self, api_client, valid_invite):
        url = reverse('verify_invite', kwargs={'version': 'v1', 'code': valid_invite.code})
        response = api_client.get(url)
        assert response.status_code == 200