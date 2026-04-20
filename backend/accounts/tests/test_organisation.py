# accounts/tests/test_organisation.py
import pytest

ORG_URL     = '/api/v1/accounts/organisation/'
MEMBERS_URL = '/api/v1/accounts/organisation/members/'


@pytest.mark.django_db
class TestOrganisationDetail:

    def test_individual_user_has_no_org(self, auth_client):
        response = auth_client.get(ORG_URL)
        assert response.status_code == 404

    def test_org_admin_gets_org(self, org_admin_client):
        response = org_admin_client.get(ORG_URL)
        assert response.status_code == 200
        assert response.data['name'] == 'Test Org'

    def test_org_admin_updates_org(self, org_admin_client):
        response = org_admin_client.patch(ORG_URL, {
            'name': 'Updated Org Name',
        }, format='json')
        assert response.status_code == 200
        assert response.data['name'] == 'Updated Org Name'

    def test_member_cannot_update_org(self, org_member_client):
        response = org_member_client.patch(ORG_URL, {
            'name': 'Hacked Name',
        }, format='json')
        assert response.status_code == 403

    def test_unauthenticated_cannot_access_org(self, api_client):
        response = api_client.get(ORG_URL)
        assert response.status_code == 401


@pytest.mark.django_db
class TestOrgMembers:

    def test_admin_gets_member_list(self, org_admin_client, org_member):
        response = org_admin_client.get(MEMBERS_URL)
        assert response.status_code == 200
        assert len(response.data) >= 1

    def test_member_cannot_get_member_list(self, org_member_client):
        response = org_member_client.get(MEMBERS_URL)
        assert response.status_code == 403

    def test_admin_cannot_remove_self(self, org_admin_client, org_admin):
        url = f'/api/v1/accounts/organisation/members/{org_admin.id}/remove/'
        response = org_admin_client.delete(url)
        assert response.status_code == 400
        assert 'yourself' in str(response.data).lower()

    def test_admin_cannot_remove_owner(self, org_admin_client, org_admin):
        url = f'/api/v1/accounts/organisation/members/{org_admin.id}/remove/'
        response = org_admin_client.delete(url)
        assert response.status_code == 400

    def test_admin_can_remove_member(self, org_admin_client, org_member):
        url = f'/api/v1/accounts/organisation/members/{org_member.id}/remove/'
        response = org_admin_client.delete(url)
        assert response.status_code == 200

    def test_member_cannot_remove_anyone(self, org_member_client, org_admin):
        url = f'/api/v1/accounts/organisation/members/{org_admin.id}/remove/'
        response = org_member_client.delete(url)
        assert response.status_code == 403

@pytest.mark.django_db
class TestOrganisationEdgeCases:
    def test_unauthenticated_cannot_get_members(self, api_client):
        response = api_client.get(MEMBERS_URL)
        assert response.status_code == 401

    def test_admin_remove_nonexistent_member(self, org_admin_client):
        fake_uuid = '00000000-0000-0000-0000-000000000000'
        url = f'/api/v1/accounts/organisation/members/{fake_uuid}/remove/'
        response = org_admin_client.delete(url)
        assert response.status_code == 404

    def test_individual_cannot_get_members(self, auth_client):
        response = auth_client.get(MEMBERS_URL)
        assert response.status_code == 403

    def test_org_member_gets_org(self, org_member_client):
        response = org_member_client.get(ORG_URL)
        assert response.status_code == 200
        assert response.data['name'] == 'Test Org'