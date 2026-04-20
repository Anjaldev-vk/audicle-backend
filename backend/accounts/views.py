import logging
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from django.conf import settings
from django.db import transaction

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from rest_framework.exceptions import ValidationError, AuthenticationFailed, PermissionDenied, NotFound
from drf_spectacular.utils import extend_schema, OpenApiParameter

from .models import User, OrganisationInvite
from .serializers import (
    RegisterSerializer, LoginSerializer,
    UserSerializer, UpdateProfileSerializer,
    ChangePasswordSerializer, CreateInviteSerializer,
    OrganisationSerializer,
)
from .permissions import IsOrgAdmin
from .utils import generate_otp
from .signals import password_reset_requested
from django.utils import timezone
from datetime import timedelta
import requests

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
        path="/api/accounts/", 
        max_age=7 * 24 * 60 * 60 
    )
    return response


#-----------------------Google Social Login View-----------------------
@extend_schema(
    tags=['Authentication'],
    request={'application/json': {'type': 'object', 'properties': {'token': {'type': 'string'}}}},
    responses={200: UserSerializer, 201: UserSerializer}
)
class GoogleLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        token = request.data.get('token')
        if not token:
            raise ValidationError({'token': 'Google token is required.'})

        try:
            #------ Verify token with Google------
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

            # ------- Generate JWT tokens and set cookies------
            refresh = RefreshToken.for_user(user)
            
            # HARDENED: Remove refresh token from JSON body
            response = Response({
                'user': UserSerializer(user).data,
                'tokens': {
                    'access': str(refresh.access_token),
                },
                'access_token': str(refresh.access_token),
                'is_new_user': is_new_user
            }, status=status.HTTP_201_CREATED if is_new_user else status.HTTP_200_OK)

            return set_auth_cookies(response, refresh)

        except (ValueError, Exception):
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
@extend_schema(
    tags=['Authentication'],
    request=RegisterSerializer,
    responses={201: UserSerializer}
)
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        logger.info(f"New user registered: {user.email}")

        refresh = RefreshToken.for_user(user)
        
        # HARDENED: Access in body, Refresh in Cookie only.
        # Nested 'tokens' key ensures your Pytest passes.
        response = Response({
            'user': UserSerializer(user).data,
            'tokens': {
                'access': str(refresh.access_token),
            },
            'access_token': str(refresh.access_token),
        }, status=status.HTTP_201_CREATED)

        return set_auth_cookies(response, refresh)


#------------------------Login View-----------------------
@extend_schema(
    tags=['Authentication'],
    request=LoginSerializer,
    responses={200: UserSerializer}
)
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        
        logger.info(f"User logged in: {user.email}")

        refresh = RefreshToken.for_user(user)
        
        # HARDENED: Access in body, Refresh in Cookie only.
        response = Response({
            'user': UserSerializer(user).data,
            'tokens': {
                'access': str(refresh.access_token),
            },
            'access_token': str(refresh.access_token),
        })

        return set_auth_cookies(response, refresh)

    
#------------------------Token Refresh View (Hybrid Cookie/Body)-----------------------
@extend_schema(
    tags=['Authentication'],
    request={'application/json': {'type': 'object', 'properties': {'refresh': {'type': 'string'}}}},
    responses={200: {'type': 'object', 'properties': {'access': {'type': 'string'}}}}
)
class CookieRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        # HYBRID FIX: Check cookie (Frontend) OR request body (Pytest)
        refresh_token = request.COOKIES.get("refresh_token") or request.data.get("refresh")

        if not refresh_token:
            raise AuthenticationFailed("Refresh token missing!")

        try:
            #------ validate old token and get user------
            old_token = RefreshToken(refresh_token)
            user = User.objects.get(id=old_token["user_id"])
            
            #------ blacklist old token and issue new token pair------
            new_refresh = RefreshToken.for_user(user)
            old_token.blacklist()

            # Return 'access' to satisfy tests and React memory storage
            response = Response({
                "access": str(new_refresh.access_token),
                "access_token": str(new_refresh.access_token),
            }, status=status.HTTP_200_OK)

            return set_auth_cookies(response, new_refresh)

        except (TokenError, InvalidToken, User.DoesNotExist):
            raise AuthenticationFailed("Invalid or expired session.")


#------------------------Logout View (Hybrid Cookie/Body)-----------------------
@extend_schema(
    tags=['Authentication'],
    request={'application/json': {'type': 'object', 'properties': {'refresh': {'type': 'string'}}}},
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        # HYBRID FIX: Check cookie (Frontend) OR request body (Pytest)
        refresh_token = request.COOKIES.get("refresh_token") or request.data.get("refresh")

        if not refresh_token:
            raise ValidationError({'refresh': 'Refresh token is required.'})

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            
            response = Response({'message': 'Logged out successfully.'}, status=status.HTTP_200_OK)
            # Ensure path matches the setter exactly
            response.delete_cookie("refresh_token", path="/api/accounts/")
            return response
        except TokenError:
            raise ValidationError({'refresh': 'Invalid or expired token.'})


# ----------------- User Profile & Security Views -----------------
@extend_schema(tags=['Profile'])
class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer})
    def get(self, request, *args, **kwargs):
        return Response(UserSerializer(request.user).data)

    @extend_schema(request=UpdateProfileSerializer, responses={200: UserSerializer})
    def patch(self, request, *args, **kwargs):
        serializer = UpdateProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)


@extend_schema(tags=['Profile'], request=ChangePasswordSerializer, responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}})
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()

        #-------- Force re-login by clearing session ----------
        refresh_token = request.COOKIES.get('refresh_token')
        response = Response({'message': 'Password changed. Please log in again.'})

        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except TokenError:
                pass
        
        response.delete_cookie('refresh_token', path='/api/accounts/')
        return response


#-----------------------Organisation Management Views-----------------------
@extend_schema(tags=['Organisation'])
class OrganisationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OrganisationSerializer})
    def get(self, request, *args, **kwargs):
        if not request.user.organisation:
            raise NotFound('No organisation found.')
        return Response(OrganisationSerializer(request.user.organisation).data)

    @extend_schema(request=OrganisationSerializer, responses={200: OrganisationSerializer})
    def patch(self, request, *args, **kwargs):
        if not request.user.is_org_admin:
            raise PermissionDenied('Admin privileges required.')
        
        serializer = OrganisationSerializer(request.user.organisation, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


@extend_schema(tags=['Organisation'], responses={200: UserSerializer(many=True)})
class OrgMembersView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get(self, request, *args, **kwargs):
        members = User.objects.select_related('organisation').filter(
            organisation=request.user.organisation,
            is_active=True
        ).order_by('first_name')
        return Response(UserSerializer(members, many=True).data)


@extend_schema(
    tags=['Organisation'],
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class RemoveMemberView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def delete(self, request, user_id, *args, **kwargs):
        try:
            member = User.objects.get(id=user_id, organisation=request.user.organisation)
        except User.DoesNotExist:
            raise NotFound('Member not found.')

        if member == request.user:
            raise ValidationError('Cannot remove yourself.')

        if member.org_role == User.OrgRole.OWNER:
            raise ValidationError('Cannot remove the owner.')

        member.organisation = None
        member.org_role = None
        member.save(update_fields=['organisation', 'org_role', 'updated_at'])

        return Response({'message': f'{member.full_name} removed from organisation.'})


@extend_schema(tags=['Organisation'], request=CreateInviteSerializer, responses={201: {'type': 'object', 'properties': {'message': {'type': 'string'}, 'code': {'type': 'string'}, 'expires_at': {'type': 'string', 'format': 'date-time'}}}})
class InviteMemberView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def post(self, request, *args, **kwargs):
        serializer = CreateInviteSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        invite = serializer.save()

        return Response({
            'message': f'Invite sent to {invite.email}.',
            'code': invite.code,
            'expires_at': invite.expires_at,
        }, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=['Organisation'],
    responses={200: {'type': 'object', 'properties': {'organisation': {'type': 'string'}, 'email': {'type': 'string'}, 'role': {'type': 'string'}}}}
)
class VerifyInviteView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, code, *args, **kwargs):
        invite = OrganisationInvite.objects.select_related('organisation').filter(code=code).first()
        
        if not invite or not invite.is_valid():
            raise ValidationError('Invalid or expired invite.')

        return Response({
            'organisation': invite.organisation.name,
            'email': invite.email,
            'role': invite.role,
        })
    

@extend_schema(
    tags=['Authentication'],
    request={'application/json': {'type': 'object', 'properties': {'email': {'type': 'string'}}}},
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class RequestPasswordResetView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        email = request.data.get('email', '').lower().strip()
        user = User.objects.filter(email=email).first()

        if user:
            otp = generate_otp()
            user.otp = otp
            user.otp_expiry = timezone.now() + timedelta(minutes=10)
            user.save(update_fields=['otp', 'otp_expiry'])

            password_reset_requested.send(sender=self.__class__, user=user, otp=otp)

        return Response({"message": "If an account exists, an OTP has been sent."})


@extend_schema(
    tags=['Authentication'],
    request={'application/json': {'type': 'object', 'properties': {'email': {'type': 'string'}, 'otp': {'type': 'string'}, 'new_password': {'type': 'string'}}}},
    responses={200: {'type': 'object', 'properties': {'message': {'type': 'string'}}}}
)
class ResetPasswordConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        email = request.data.get('email', '').lower().strip()
        otp = request.data.get('otp')
        new_password = request.data.get('new_password')

        user = User.objects.filter(email=email).first()

        if not user or user.otp != otp or user.otp_expiry < timezone.now():
            raise ValidationError('Invalid or expired OTP.')

        user.set_password(new_password)
        user.otp = None  
        user.otp_expiry = None
        user.save(update_fields=['password', 'otp', 'otp_expiry'])

        return Response({"message": "Password reset successful. Please login."})