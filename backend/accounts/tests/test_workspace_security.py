import pytest
import uuid
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from accounts.models import Organisation, Membership

@pytest.mark.django_db
class TestWorkspaceSecurity:
    def test_unauthorized_workspace_access_blocked(self, individual_user):
        """
        GIVEN an authenticated user
        WHEN they provide a Workspace ID of an organization they DON'T belong to
        THEN the request should be blocked with 403 Forbidden.
        """
        client = APIClient()
        client.force_authenticate(user=individual_user)
        
        # Create an org that the user DOES NOT belong to
        other_org = Organisation.objects.create(name="Other Org", slug="other-org")
        
        # Try to access a legitimate endpoint with the other org's ID
        url = reverse('meetings:meeting-list-create')
        
        # We must use extra headers in the request instead of client.credentials
        # because some versions of DRF test client handles them differently
        response = client.get(url, HTTP_X_WORKSPACE_ID=str(other_org.id))
        
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()['code'] == 'unauthorized_workspace'

    def test_valid_workspace_access_allowed(self, individual_user):
        client = APIClient()
        client.force_authenticate(user=individual_user)
        
        org = Organisation.objects.create(name="My Org", slug="my-org")
        Membership.objects.create(user=individual_user, organisation=org, role='admin')
        
        url = reverse('meetings:meeting-list-create')
        response = client.get(url, HTTP_X_WORKSPACE_ID=str(org.id))
        
        assert response.status_code == status.HTTP_200_OK

    def test_personal_workspace_access_allowed(self, individual_user):
        client = APIClient()
        client.force_authenticate(user=individual_user)
        
        url = reverse('meetings:meeting-list-create')
        response = client.get(url, HTTP_X_WORKSPACE_ID='personal')
        
        assert response.status_code == status.HTTP_200_OK

    def test_invalid_uuid_is_blocked(self, individual_user):
        client = APIClient()
        client.force_authenticate(user=individual_user)
        
        url = reverse('meetings:meeting-list-create')
        response = client.get(url, HTTP_X_WORKSPACE_ID='not-a-uuid')
        
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()['code'] == 'invalid_workspace'
