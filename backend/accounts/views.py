import logging
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from django.conf import settings
from django.db import transaction
from django.core.cache import cache

from rest_framework import status, generics
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

from .models import User, OrganisationInvite, Organisation, Membership
from .serializers import (
    RegisterUserSerializer, LoginUserSerializer,
    UserSerializer, UpdateUserProfileSerializer,
    ChangeUserPasswordSerializer, CreateOrganisationInviteSerializer,
    OrganisationSerializer, UpdateOrganisationSerializer,
    GoogleLoginSerializer, RequestPasswordResetSerializer,
    ResetPasswordConfirmSerializer, CookieRefreshSerializer,
    OrganisationCreateSerializer,
)
from .permissions import IsOrgAdmin
from .utils import generate_otp, get_workspaces_for_user
from .signals import password_reset_requested
from accounts.mfa_utils import generate_mfa_token
from utils.response import success_response, error_response
from utils.cache_keys import (
    user_profile_key, user_workspaces_key,
    org_profile_key, org_members_key,
    invalidate_user_cache, invalidate_org_cache,
)
from utils.pagination import StandardPagination

logger = logging.getLogger(__name__)


# -----------------------Helper Function to Set Auth Cookies-----------------------
def set_auth_cookies(response, refresh_token):
    response.set_cookie(
        key="refresh_token",
        value=str(refresh_token),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path="/",
        max_age=7 * 24 * 60 * 60
    )
    return response


# -----------------------Google Social Login View-----------------------
@extend_schema(
    tags=['Authentication'],
    request=GoogleLoginSerializer,
    responses={200: UserSerializer, 201: UserSerializer}
)
class GoogleLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request, *args, **kwargs):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']

        try:
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

            if user.mfa_enabled:
                mfa_token = generate_mfa_token(str(user.id))
                return success_response(
                    message="MFA verification required.",
                    data={"mfa_required": True, "mfa_token": mfa_token},
                    status_code=status.HTTP_200_OK,
                )

            refresh = RefreshToken.for_user(user)
            logger.info("User logged in via Google: %s", user.email)

            response = success_response(
                message="Google login successful.",
                data={
                    'user': UserSerializer(user).data,
                    'tokens': {'access': str(refresh.access_token)},
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
            with transaction.atomic():
                Membership.objects.get_or_create(
                    user=user,
                    organisation=invite.organisation,
                    defaults={'role': invite.role}
                )
                invite.status = OrganisationInvite.Status.ACCEPTED
                invite.save(update_fields=['status'])


# ------------------------Registration View-----------------------
@extend_schema(
    tags=['Authentication'],
    request=RegisterUserSerializer,
    responses={201: UserSerializer}
)
class RegisterView(APIView):
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
                'tokens': {'access': str(refresh.access_token)},
                'access_token': str(refresh.access_token),
                "workspaces": get_workspaces_for_user(user),
                "active_workspace": "personal",
            },
            status_code=status.HTTP_201_CREATED
        )

        return set_auth_cookies(response, refresh)


# ------------------------Login View-----------------------
@extend_schema(
    tags=['Authentication'],
    request=LoginUserSerializer,
    responses={200: UserSerializer}
)
class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request, *args, **kwargs):
        serializer = LoginUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']

        logger.info("User logged in: %s", user.email)

        if user.mfa_enabled:
            mfa_token = generate_mfa_token(str(user.id))
            return success_response(
                message="MFA verification required.",
                data={"mfa_required": True, "mfa_token": mfa_token},
                status_code=status.HTTP_200_OK,
            )

        refresh = RefreshToken.for_user(user)

        response = success_response(
            message="Login successful.",
            data={
                "access_token": str(refresh.access_token),
                "user": UserSerializer(user).data,
                "workspaces": get_workspaces_for_user(user),
                "active_workspace": "personal",
            },
            status_code=status.HTTP_200_OK
        )

        return set_auth_cookies(response, refresh)


# ------------------------Token Refresh View-----------------------
@extend_schema(
    tags=['Authentication'],
    request=CookieRefreshSerializer,
    responses={200: {'type': 'object', 'properties': {'access': {'type': 'string'}}}}
)
class CookieRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = CookieRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        refresh_token = request.COOKIES.get("refresh_token") or serializer.validated_data.get("refresh")

        if not refresh_token:
            return error_response(
                message="Refresh token missing!",
                code="missing_token",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        try:
            old_token = RefreshToken(refresh_token)
            user = User.objects.get(id=old_token["user_id"])
            new_refresh = RefreshToken.for_user(user)
            old_token.blacklist()

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


# ------------------------Logout View-----------------------
@extend_schema(
    tags=['Authentication'],
    request=CookieRefreshSerializer,
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = CookieRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

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
            response.delete_cookie("refresh_token", path="/")
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
    GET  /api/v1/accounts/me/  — Returns profile (cached 5 min)
    PATCH /api/v1/accounts/me/ — Updates profile + invalidates cache
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer})
    def get(self, request, *args, **kwargs):
        key = user_profile_key(request.user.id)
        cached = cache.get(key)

        if cached:
            logger.info('user profile cache hit user=%s', request.user.id)
            return success_response(
                message="Profile retrieved successfully.",
                data=cached,
                status_code=status.HTTP_200_OK
            )

        data = {
            **UserSerializer(request.user).data,
            "workspaces": get_workspaces_for_user(request.user),
            "active_workspace": getattr(request, 'workspace_type', 'personal'),
        }
        cache.set(key, data, timeout=300)
        logger.info('user profile cache set user=%s', request.user.id)

        return success_response(
            message="Profile retrieved successfully.",
            data=data,
            status_code=status.HTTP_200_OK
        )

    @extend_schema(request=UpdateUserProfileSerializer, responses={200: UserSerializer})
    def patch(self, request, *args, **kwargs):
        serializer = UpdateUserProfileSerializer(
            request.user, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Invalidate profile + workspace cache
        invalidate_user_cache(request.user.id)
        logger.info('user profile updated cache invalidated user=%s', request.user.email)

        # Return fresh data (do not re-cache here — next GET will set it)
        data = {
            **UserSerializer(request.user).data,
            "workspaces": get_workspaces_for_user(request.user),
            "active_workspace": getattr(request, 'workspace_type', 'personal'),
        }

        return success_response(
            message="Profile updated successfully.",
            data=data,
            status_code=status.HTTP_200_OK
        )


@extend_schema(
    tags=['Profile'],
    request=ChangeUserPasswordSerializer,
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ChangeUserPasswordSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        logger.info("User password changed: %s", request.user.email)

        # Invalidate profile cache on password change
        invalidate_user_cache(request.user.id)

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

        response.delete_cookie('refresh_token', path='/')
        return response


# -----------------------Organisation Management Views-----------------------
@extend_schema(tags=['Organisation'])
class OrganisationDetailView(APIView):
    """
    GET  /api/v1/accounts/organisation/ — Returns org details (cached 5 min)
    PATCH /api/v1/accounts/organisation/ — Updates org + invalidates cache
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OrganisationSerializer})
    def get(self, request, *args, **kwargs):
        if not request.organisation:
            return success_response(
                message="Personal workspace active.",
                data=None,
                status_code=status.HTTP_200_OK
            )

        key = org_profile_key(request.organisation.id)
        cached = cache.get(key)

        if cached:
            logger.info('org profile cache hit org=%s', request.organisation.id)
            return success_response(
                message="Organisation retrieved successfully.",
                data=cached,
                status_code=status.HTTP_200_OK
            )

        data = OrganisationSerializer(request.organisation).data
        cache.set(key, data, timeout=300)
        logger.info('org profile cache set org=%s', request.organisation.id)

        return success_response(
            message="Organisation retrieved successfully.",
            data=data,
            status_code=status.HTTP_200_OK
        )

    @extend_schema(request=UpdateOrganisationSerializer, responses={200: OrganisationSerializer})
    def patch(self, request, *args, **kwargs):
        if not request.membership or request.membership.role not in ('owner', 'admin'):
            return error_response(
                message="Admin privileges required.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN
            )

        serializer = UpdateOrganisationSerializer(
            request.organisation, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Invalidate org cache + all member workspace caches
        invalidate_org_cache(request.organisation.id)
        for m in request.organisation.memberships.select_related('user'):
            invalidate_user_cache(m.user.id)

        logger.info(
            'org updated cache invalidated org=%s by user=%s',
            request.organisation.slug,
            request.user.email
        )

        return success_response(
            message="Organisation updated successfully.",
            data=OrganisationSerializer(request.organisation).data,
            status_code=status.HTTP_200_OK
        )


@extend_schema(tags=['Organisation'], responses={200: UserSerializer(many=True)})
class OrgMembersView(APIView):
    """
    GET /api/v1/accounts/organisation/members/ — Returns members (cached 5 min, paginated)
    """
    permission_classes = [IsAuthenticated, IsOrgAdmin]
    pagination_class = StandardPagination

    def get(self, request, *args, **kwargs):
        key = org_members_key(request.organisation.id)
        data = cache.get(key)

        if not data:
            memberships = Membership.objects.filter(
                organisation=request.organisation
            ).select_related('user').order_by('user__first_name')

            data = []
            for m in memberships:
                user_data = UserSerializer(m.user).data
                user_data['role'] = m.role
                data.append(user_data)

            cache.set(key, data, timeout=300)
            logger.info('org members cache set org=%s', request.organisation.id)
        else:
            logger.info('org members cache hit org=%s', request.organisation.id)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(data, request)
        return paginator.get_paginated_response(page)


@extend_schema(
    tags=['Organisation'],
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class RemoveMemberView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def delete(self, request, user_id, *args, **kwargs):
        try:
            membership = Membership.objects.get(
                user_id=user_id,
                organisation=request.organisation
            )
        except Membership.DoesNotExist:
            return error_response(
                message="Member not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND
            )

        if membership.user == request.user:
            return error_response(
                message="Cannot remove yourself.",
                code="invalid_action",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        if membership.role == Membership.Role.OWNER:
            return error_response(
                message="Cannot remove the owner.",
                code="invalid_action",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        membership.delete()
        # Signal fires automatically → invalidates user + org cache

        logger.info(
            'member removed org=%s removed_user=%s by=%s',
            request.organisation.id,
            membership.user.email,
            request.user.email
        )

        return success_response(
            message="%s removed from organisation." % membership.user.full_name,
            data={},
            status_code=status.HTTP_200_OK
        )


@extend_schema(
    tags=['Organisation'],
    request=CreateOrganisationInviteSerializer,
    responses={201: {'type': 'object', 'properties': {
        'message': {'type': 'string'},
        'code': {'type': 'string'},
        'expires_at': {'type': 'string', 'format': 'date-time'}
    }}}
)
class InviteMemberView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def post(self, request, *args, **kwargs):
        serializer = CreateOrganisationInviteSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        invite = serializer.save()

        logger.info(
            'invite sent email=%s org=%s by=%s',
            invite.email,
            request.organisation.id,
            request.user.email
        )

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
    responses={200: {'type': 'object', 'properties': {
        'organisation': {'type': 'string'},
        'email': {'type': 'string'},
        'role': {'type': 'string'}
    }}}
)
class VerifyInviteView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, code, *args, **kwargs):
        invite = OrganisationInvite.objects.select_related('organisation').filter(
            code=code
        ).first()

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


# -----------------------Workspace Views-----------------------
class WorkspaceListView(generics.GenericAPIView):
    """
    GET /api/v1/accounts/workspaces/ — Returns all workspaces (cached 5 min, paginated)
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request, *args, **kwargs):
        key = user_workspaces_key(request.user.id)
        data = cache.get(key)

        if not data:
            data = get_workspaces_for_user(request.user)
            cache.set(key, data, timeout=300)
            logger.info('workspaces cache set user=%s', request.user.id)
        else:
            logger.info('workspaces cache hit user=%s', request.user.id)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(data, request)
        return paginator.get_paginated_response(page)


class WorkspaceCreateView(generics.GenericAPIView):
    """
    POST /api/v1/accounts/workspaces/create/ — Creates a new org workspace
    Invalidates workspace cache so next GET returns fresh list.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = OrganisationCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            org = Organisation.objects.create(
                name=serializer.validated_data['name'],
                slug=serializer.validated_data['slug'],
                plan='free',
            )
            Membership.objects.create(
                user=request.user,
                organisation=org,
                role=Membership.Role.OWNER,
            )
            # Signal fires → invalidates user + org cache automatically

        logger.info(
            'workspace created org=%s by user=%s',
            org.slug,
            request.user.email
        )

        return success_response(
            message="Organisation created.",
            data={"workspaces": get_workspaces_for_user(request.user)},
            status_code=201
        )


class InviteAcceptView(generics.GenericAPIView):
    """
    POST /api/v1/accounts/invites/<code>/accept/
    Accepts an invite and creates a Membership.
    Invalidates workspace cache so new org appears immediately.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, code, *args, **kwargs):
        try:
            invite = OrganisationInvite.objects.select_related(
                'organisation'
            ).get(code=code, status='pending')
        except OrganisationInvite.DoesNotExist:
            return error_response(
                code="invalid_invite",
                message="Invite not found or already used."
            )

        if invite.expires_at < timezone.now():
            invite.status = OrganisationInvite.Status.EXPIRED
            invite.save(update_fields=['status'])
            return error_response(
                code="invite_expired",
                message="This invite has expired."
            )

        with transaction.atomic():
            Membership.objects.get_or_create(
                user=request.user,
                organisation=invite.organisation,
                defaults={'role': invite.role}
            )
            # Signal fires → invalidates user + org cache automatically
            invite.status = OrganisationInvite.Status.ACCEPTED
            invite.save(update_fields=['status'])

        logger.info(
            'invite accepted org=%s user=%s',
            invite.organisation.id,
            request.user.email
        )

        return success_response(
            message="Joined organisation successfully.",
            data={"workspaces": get_workspaces_for_user(request.user)}
        )
