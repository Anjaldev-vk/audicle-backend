import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

REFRESH_URL = '/api/v1/accounts/token/refresh/'
LOGOUT_URL  = '/api/v1/accounts/logout/'

@pytest.mark.django_db
class TestTokens:
    """
    Tests for JWT Token management including Refresh and Logout.
    Validates both the modern Cookie-based path and the legacy Body-based path.
    """

    def test_refresh_token_via_body(self, api_client, individual_user):
        """
        Problem: Legacy clients or test suites need to refresh via JSON body.
        Requirement: The view must accept 'refresh' in request.data.
        """
        refresh = RefreshToken.for_user(individual_user)
        response = api_client.post(REFRESH_URL, {
            'refresh': str(refresh),
        }, format='json')
        
        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data['data']

    def test_refresh_token_via_cookie(self, api_client, individual_user):
        """
        Problem: React frontend stores tokens in HttpOnly cookies for XSS protection.
        Requirement: The view must read 'refresh_token' from request.COOKIES.
        """
        refresh = RefreshToken.for_user(individual_user)
        
        api_client.cookies['refresh_token'] = str(refresh)
        
        response = api_client.post(REFRESH_URL, {}, format='json')
        
        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data['data']
        
        assert 'refresh_token' in response.cookies
        assert 'refresh' not in response.data['data']

    def test_logout_via_cookie_clears_cookie(self, api_client, individual_user):
        """
        Problem: Logout must neutralize the session and tell the browser to delete the cookie.
        Requirement: Use response.delete_cookie() on the correct path.
        """
        refresh = RefreshToken.for_user(individual_user)
        api_client.force_authenticate(user=individual_user)
        
        api_client.cookies['refresh_token'] = str(refresh)
        
        response = api_client.post(LOGOUT_URL)
        
        assert response.status_code == status.HTTP_200_OK
        assert response.cookies['refresh_token'].value == ""

    def test_blacklisted_token_rejected(self, api_client, individual_user):
        """
        Problem: A used or logged-out token must never be usable again.
        Requirement: JWT Blacklisting must be active in settings.
        """
        refresh = RefreshToken.for_user(individual_user)
        api_client.force_authenticate(user=individual_user)

        api_client.post(LOGOUT_URL, {'refresh': str(refresh)})

        response = api_client.post(REFRESH_URL, {'refresh': str(refresh)})
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED]

    def test_logout_without_token_fails(self, api_client, individual_user):
        """
        Problem: Random logout requests without tokens should not return 200 OK.
        Requirement: Backend must validate token presence before success message.
        """
        api_client.force_authenticate(user=individual_user)
        api_client.cookies.clear() 
        
        response = api_client.post(LOGOUT_URL, {}, format='json')
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST

@pytest.mark.django_db
class TestTokenEdgeCases:
    def test_invalid_token_rejected(self, api_client):
        response = api_client.post(REFRESH_URL, {
            'refresh': 'totally.fake.token',
        }, format='json')
        assert response.status_code in [400, 401]

    def test_logout_unauthenticated(self, api_client):
        response = api_client.post(LOGOUT_URL, {})
        assert response.status_code == 401

    def test_protected_endpoint_with_invalid_token(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION='Bearer fake.invalid.token')
        response = api_client.get('/api/v1/accounts/me/')
        assert response.status_code == 401