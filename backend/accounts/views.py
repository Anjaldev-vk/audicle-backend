import logging
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from django.conf import settings
from django.db import transaction

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from rest_framework.exceptions import ValidationError, AuthenticationFailed, PermissionDenied, NotFound
from rest_framework.throttling import ScopedRateThrottle
from drf_spectacular.utils import extend_schema, OpenApiParameter
from django.utils import timezone
from datetime import timedelta
import requests

from .models import User, OrganisationInvite
from .serializers import (
    RegisterUserSerializer, LoginUserSerializer,
    UserSerializer, UpdateUserProfileSerializer,
    ChangeUserPasswordSerializer, CreateOrganisationInviteSerializer,
    OrganisationSerializer, UpdateOrganisationSerializer,
    GoogleLoginSerializer, RequestPasswordResetSerializer,
    ResetPasswordConfirmSerializer, CookieRefreshSerializer,
)
from .permissions import IsOrgAdmin
from .utils import generate_otp
from .signals import password_reset_requested
from accounts.mfa_utils import generate_mfa_token
from utils.response import success_response, error_response

logger = logging.getLogger(__name__)

#-----------------------Helper Function to Set Auth Cookies-----------------------
def set_auth_cookies(response, refresh_token):
    """
    Sets the refresh token in an HttpOnly cookie.
    Path is set to /api/accounts/ so both Refresh and Logout views can access it.
    """
    response.set_cookie(
        key="refresh_token",
        value=str(refresh_token),
        httponly=True,
        secure=not settings.DEBUG, 
        samesite="Lax",      
        path="/api/", 
        max_age=7 * 24 * 60 * 60 
    )
    return response


#-----------------------Google Social Login View-----------------------
# Purpose: Authenticates user via Google OAuth2, creates a user if not exists, and handles pending invites.
@extend_schema(
    tags=['Authentication'],
    request=GoogleLoginSerializer,
    responses={200: UserSerializer, 201: UserSerializer}
)
class GoogleLoginView(APIView):
    """
    POST /api/v1/accounts/google/login/

    Authenticates a user using a Google OAuth2 token.
    Creates a new user if one doesn't exist.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request, *args, **kwargs):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']

        try:
            # ------- Verify token with Google and get user info------
            user_info_response = requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {token}'}
            )

            if not user_info_response.ok:
                raise AuthenticationFailed('Google authentication failed.')
                
            id_info = user_info_response.json()

            if not id_info.get('email_verified'):
                raise AuthenticationFailed('Google email not verified.')

            email = id_info.get('email', '').lower().strip()

            # ------- Atomic user creation/retrieval--------
            with transaction.atomic():
                user = User.objects.filter(email=email).first()
                is_new_user = False

                if not user:
                    user = User.objects.create_user(
                        email=email,
                        first_name=id_info.get('given_name', ''),
                        last_name=id_info.get('family_name', ''),
                        password=None,
                    )
                    user.set_unusable_password()
                    user.is_verified = True
                    user.save(update_fields=['is_verified'])
                    is_new_user = True
                    self._process_pending_invite(user)

            # ----- MFA intercept ----------
            if user.mfa_enabled:
                mfa_token = generate_mfa_token(str(user.id))
                return success_response(
                    message="MFA verification required.",
                    data={
                        "mfa_required": True,
                        "mfa_token": mfa_token,
                    },
                    status_code=status.HTTP_200_OK,
                )

            # ------- Generate JWT tokens and set cookies------
            refresh = RefreshToken.for_user(user)
            
            logger.info("User logged in via Google: %s", user.email)

            response = success_response(
                message="Google login successful.",
                data={
                    'user': UserSerializer(user).data,
                    'tokens': {
                        'access': str(refresh.access_token),
                    },
                    'access_token': str(refresh.access_token),
                    'is_new_user': is_new_user
                },
                status_code=status.HTTP_201_CREATED if is_new_user else status.HTTP_200_OK
            )

            return set_auth_cookies(response, refresh)

        except (ValueError, Exception) as e:
            logger.error("Google login failed: %s", str(e))
            raise AuthenticationFailed('Google authentication failed.')

    def _process_pending_invite(self, user):
        invite = OrganisationInvite.objects.select_related('organisation').filter(
            email=user.email,
            status=OrganisationInvite.Status.PENDING
        ).first()

        if invite and invite.is_valid():
            user.organisation = invite.organisation
            user.org_role = invite.role
            user.save(update_fields=['organisation', 'org_role', 'updated_at'])
            invite.status = OrganisationInvite.Status.ACCEPTED
            invite.save(update_fields=['status'])



#------------------------Registration View-----------------------
# Purpose: Creates a new user account and returns a set of JWT tokens with HttpOnly cookies.
@extend_schema(
    tags=['Authentication'],
    request=RegisterUserSerializer,
    responses={201: UserSerializer}
)
class RegisterView(APIView):
    """
    POST /api/v1/accounts/register/
    
    Registers a new user account.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request, *args, **kwargs):
        serializer = RegisterUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        logger.info("New user registered: %s", user.email)

        refresh = RefreshToken.for_user(user)
        
        response = success_response(
            message="Registration successful.",
            data={
                'user': UserSerializer(user).data,
                'tokens': {
                    'access': str(refresh.access_token),
                },
                'access_token': str(refresh.access_token),
            },
            status_code=status.HTTP_201_CREATED
        )

        return set_auth_cookies(response, refresh)


#------------------------Login View-----------------------
@extend_schema(
    tags=['Authentication'],
    request=LoginUserSerializer,
    responses={200: UserSerializer}
)
class LoginView(APIView):
    """
    POST /api/v1/accounts/login/
    
    Authenticates a user with email and password.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request, *args, **kwargs):
        serializer = LoginUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        
        logger.info("User logged in: %s", user.email)

        # MFA intercept
        if user.mfa_enabled:
            mfa_token = generate_mfa_token(str(user.id))
            return success_response(
                message="MFA verification required.",
                data={
                    "mfa_required": True,
                    "mfa_token": mfa_token,
                },
                status_code=status.HTTP_200_OK,
            )

        refresh = RefreshToken.for_user(user)
        
        response = success_response(
            message="Login successful.",
            data={
                'user': UserSerializer(user).data,
                'tokens': {
                    'access': str(refresh.access_token),
                },
                'access_token': str(refresh.access_token),
            },
            status_code=status.HTTP_200_OK
        )

        return set_auth_cookies(response, refresh)

    
#------------------------Token Refresh View (Hybrid Cookie/Body)-----------------------
@extend_schema(
    tags=['Authentication'],
    request=CookieRefreshSerializer,
    responses={200: {'type': 'object', 'properties': {'access': {'type': 'string'}}}}
)
class CookieRefreshView(APIView):
    """
    POST /api/v1/accounts/token/refresh/
    
    Refreshes the access token using a refresh token from cookies or request body.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = CookieRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # HYBRID FIX: Check cookie (Frontend) OR request body (Pytest)
        refresh_token = request.COOKIES.get("refresh_token") or serializer.validated_data.get("refresh")

        if not refresh_token:
            return error_response(
                message="Refresh token missing!",
                code="missing_token",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        try:
            #------ validate old token and get user------
            old_token = RefreshToken(refresh_token)
            user = User.objects.get(id=old_token["user_id"])
            
            #------ blacklist old token and issue new token pair------
            new_refresh = RefreshToken.for_user(user)
            old_token.blacklist()

            # Return 'access' to satisfy tests and React memory storage
            response = success_response(
                message="Token refreshed successfully.",
                data={
                    "access": str(new_refresh.access_token),
                    "access_token": str(new_refresh.access_token),
                },
                status_code=status.HTTP_200_OK
            )

            return set_auth_cookies(response, new_refresh)

        except (TokenError, InvalidToken, User.DoesNotExist):
            return error_response(
                message="Invalid or expired session.",
                code="invalid_token",
                status_code=status.HTTP_401_UNAUTHORIZED
            )


#------------------------Logout View (Hybrid Cookie/Body)-----------------------
@extend_schema(
    tags=['Authentication'],
    request=CookieRefreshSerializer,
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class LogoutView(APIView):
    """
    POST /api/v1/accounts/logout/
    
    Logs out the user by blacklisting the refresh token and clearing the cookie.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = CookieRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # HYBRID FIX: Check cookie (Frontend) OR request body (Pytest)
        refresh_token = request.COOKIES.get("refresh_token") or serializer.validated_data.get("refresh")

        if not refresh_token:
            return error_response(
                message="Refresh token is required for logout.",
                code="missing_token",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            
            response = success_response(
                message="Logged out successfully.",
                data={},
                status_code=status.HTTP_200_OK
            )
            # Ensure path matches the setter exactly
            response.delete_cookie("refresh_token", path="/api/")
            return response
        except TokenError:
            return error_response(
                message="Invalid or expired token.",
                code="invalid_token",
                status_code=status.HTTP_400_BAD_REQUEST
            )


# ----------------- User Profile & Security Views -----------------
@extend_schema(tags=['Profile'])
class MeView(APIView):
    """
    GET /api/v1/accounts/me/
    PATCH /api/v1/accounts/me/
    
    GET: Returns the current user's profile.
    PATCH: Updates the current user's profile fields.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer})
    def get(self, request, *args, **kwargs):
        return success_response(
            message="Profile retrieved successfully.",
            data=UserSerializer(request.user).data,
            status_code=status.HTTP_200_OK
        )

    @extend_schema(request=UpdateUserProfileSerializer, responses={200: UserSerializer})
    def patch(self, request, *args, **kwargs):
        serializer = UpdateUserProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        logger.info("User profile updated: %s", request.user.email)
        
        return success_response(
            message="Profile updated successfully.",
            data=UserSerializer(request.user).data,
            status_code=status.HTTP_200_OK
        )


@extend_schema(tags=['Profile'], request=ChangeUserPasswordSerializer, responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}})
class ChangePasswordView(APIView):
    """
    POST /api/v1/accounts/password/change/
    
    Changes the authenticated user's password.
    Forces a logout by clearing cookies and blacklisting the current refresh token.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ChangeUserPasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()

        logger.info("User password changed: %s", request.user.email)

        #-------- Force re-login by clearing session ----------
        refresh_token = request.COOKIES.get('refresh_token')
        
        response = success_response(
            message="Password changed. Please log in again.",
            data={},
            status_code=status.HTTP_200_OK
        )

        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except TokenError:
                pass
        
        response.delete_cookie('refresh_token', path='/api/')
        return response


#-----------------------Organisation Management Views-----------------------
@extend_schema(tags=['Organisation'])
class OrganisationDetailView(APIView):
    """
    GET /api/v1/accounts/organisation/
    PATCH /api/v1/accounts/organisation/
    
    GET: Returns details of the current user's organisation.
    PATCH: Updates organisation details (Admin only).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OrganisationSerializer})
    def get(self, request, *args, **kwargs):
        if not request.user.organisation:
            return error_response(
                message="No organisation found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND
            )
        return success_response(
            message="Organisation retrieved successfully.",
            data=OrganisationSerializer(request.user.organisation).data,
            status_code=status.HTTP_200_OK
        )

    @extend_schema(request=UpdateOrganisationSerializer, responses={200: OrganisationSerializer})
    def patch(self, request, *args, **kwargs):
        if not request.user.is_org_admin:
            return error_response(
                message="Admin privileges required.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN
            )
        
        serializer = UpdateOrganisationSerializer(request.user.organisation, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        logger.info("Organisation updated: %s by %s", request.user.organisation.slug, request.user.email)
        
        return success_response(
            message="Organisation updated successfully.",
            data=OrganisationSerializer(request.user.organisation).data,
            status_code=status.HTTP_200_OK
        )


@extend_schema(tags=['Organisation'], responses={200: UserSerializer(many=True)})
class OrgMembersView(APIView):
    """
    GET /api/v1/accounts/organisation/members/
    
    Returns a list of all members in the current user's organisation.
    """
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get(self, request, *args, **kwargs):
        members = User.objects.select_related('organisation').filter(
            organisation=request.user.organisation,
            is_active=True
        ).order_by('first_name')
        
        return success_response(
            message="Organisation members retrieved successfully.",
            data=UserSerializer(members, many=True).data,
            status_code=status.HTTP_200_OK
        )


@extend_schema(
    tags=['Organisation'],
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class RemoveMemberView(APIView):
    """
    DELETE /api/v1/accounts/organisation/members/<user_id>/
    
    Removes a member from the organisation.
    """
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def delete(self, request, user_id, *args, **kwargs):
        try:
            member = User.objects.get(id=user_id, organisation=request.user.organisation)
        except User.DoesNotExist:
            return error_response(
                message="Member not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND
            )

        if member == request.user:
            return error_response(
                message="Cannot remove yourself.",
                code="invalid_action",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        if member.org_role == User.OrgRole.OWNER:
            return error_response(
                message="Cannot remove the owner.",
                code="invalid_action",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        member.organisation = None
        member.org_role = None
        member.save(update_fields=['organisation', 'org_role', 'updated_at'])
        
        logger.info("Member removed from organisation: %s by %s", member.email, request.user.email)

        return success_response(
            message="%s removed from organisation." % member.full_name,
            data={},
            status_code=status.HTTP_200_OK
        )


@extend_schema(tags=['Organisation'], request=CreateOrganisationInviteSerializer, responses={201: {'type': 'object', 'properties': {'message': {'type': 'string'}, 'code': {'type': 'string'}, 'expires_at': {'type': 'string', 'format': 'date-time'}}}})
class InviteMemberView(APIView):
    """
    POST /api/v1/accounts/organisation/invites/
    
    Sends an invitation to join the organisation.
    """
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def post(self, request, *args, **kwargs):
        serializer = CreateOrganisationInviteSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        invite = serializer.save()
        
        logger.info("Organisation invite sent to %s by %s", invite.email, request.user.email)

        return success_response(
            message="Invite sent to %s." % invite.email,
            data={
                'code': invite.code,
                'expires_at': invite.expires_at,
            },
            status_code=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=['Organisation'],
    responses={200: {'type': 'object', 'properties': {'organisation': {'type': 'string'}, 'email': {'type': 'string'}, 'role': {'type': 'string'}}}}
)
class VerifyInviteView(APIView):
    """
    GET /api/v1/accounts/organisation/invites/<code/verify/
    
    Verifies an invitation code and returns organisation details.
    """
    permission_classes = [AllowAny]

    def get(self, request, code, *args, **kwargs):
        invite = OrganisationInvite.objects.select_related('organisation').filter(code=code).first()
        
        if not invite or not invite.is_valid():
            return error_response(
                message="Invalid or expired invite.",
                code="invalid_invite",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        return success_response(
            message="Invite verified.",
            data={
                'organisation': invite.organisation.name,
                'email': invite.email,
                'role': invite.role,
            },
            status_code=status.HTTP_200_OK
        )
    

@extend_schema(
    tags=['Authentication'],
    request=RequestPasswordResetSerializer,
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class RequestPasswordResetView(APIView):
    """
    POST /api/v1/accounts/password/reset/request/
    
    Requests a password reset OTP for a given email.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request, *args, **kwargs):
        serializer = RequestPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        
        user = User.objects.filter(email=email).first()

        if user:
            otp = generate_otp()
            user.otp = otp
            user.otp_expiry = timezone.now() + timedelta(minutes=10)
            user.save(update_fields=['otp', 'otp_expiry'])

            password_reset_requested.send(sender=self.__class__, user=user, otp=otp)
            logger.info("Password reset requested for: %s", email)

        return success_response(
            message="If an account exists, an OTP has been sent.",
            data={},
            status_code=status.HTTP_200_OK
        )


@extend_schema(
    tags=['Authentication'],
    request=ResetPasswordConfirmSerializer,
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class ResetPasswordConfirmView(APIView):
    """
    POST /api/v1/accounts/password/reset/confirm/
    
    Resets the password using an OTP.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = ResetPasswordConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data['email']
        otp = serializer.validated_data['otp']
        new_password = serializer.validated_data['new_password']

        user = User.objects.filter(email=email).first()

        if not user or user.otp != otp or user.otp_expiry < timezone.now():
            return error_response(
                message="Invalid or expired OTP.",
                code="invalid_otp",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.otp = None  
        user.otp_expiry = None
        user.save(update_fields=['password', 'otp', 'otp_expiry'])
        
        logger.info("Password reset successful for: %s", email)

        return success_response(
            message="Password reset successful. Please login.",
            data={},
            status_code=status.HTTP_200_OK
        )
