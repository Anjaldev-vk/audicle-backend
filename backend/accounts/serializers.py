from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.db import transaction
from datetime import timedelta
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema_field
from .models import Organisation, OrganisationInvite, User 


#---------------------Helper Function to Generate JWT Tokens---------------------
def get_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {
        'access':  str(refresh.access_token),
        'refresh': str(refresh),
    }


#---------------------Organisation Serializer (Nested in User Profile)-----------------
class OrganisationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Organisation
        fields = ['id', 'name', 'slug', 'plan', 'logo_url']


#-----------------------User Serializer-----------------------
class UserSerializer(serializers.ModelSerializer):
    full_name    = serializers.SerializerMethodField()
    organisation = OrganisationSerializer(read_only=True)
    account_type = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'phone_number', 'job_title', 'timezone', 'avatar_url',
            'email_notifications', 'meeting_reminders',
            'is_verified', 'organisation', 'org_role',
            'account_type', 'created_at', 'mfa_enabled'
        ]
        read_only_fields = ['id', 'email', 'is_verified', 'created_at', 'mfa_enabled']

    @extend_schema_field(serializers.CharField)
    def get_full_name(self, obj):
        return obj.full_name

    @extend_schema_field(serializers.CharField)
    def get_account_type(self, obj):
        return 'individual' if obj.is_individual else 'organisation'


#-----------------------Registration Serializer-----------------------
class RegisterUserSerializer(serializers.Serializer):
    email            = serializers.EmailField()
    first_name       = serializers.CharField(max_length=150)
    last_name        = serializers.CharField(max_length=150)
    password         = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    account_type = serializers.ChoiceField(
        choices=['individual', 'create_org', 'join_org'],
        default='individual'
    )

    org_name    = serializers.CharField(max_length=255, required=False, allow_blank=True)
    org_slug    = serializers.SlugField(max_length=100, required=False, allow_blank=True)
    invite_code = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate_email(self, value):
        value = value.lower().strip()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({
                'confirm_password': 'Passwords do not match.'
            })

        account_type = attrs.get('account_type', 'individual')

        if account_type == 'create_org':
            if not attrs.get('org_name') or not attrs.get('org_slug'):
                raise serializers.ValidationError({
                    'org_name': 'Organisation name and slug are required.'
                })
            if Organisation.objects.filter(slug=attrs['org_slug']).exists():
                raise serializers.ValidationError({
                    'org_slug': 'This slug is already taken.'
                })

        if account_type == 'join_org':
            code = attrs.get('invite_code')
            if not code:
                raise serializers.ValidationError({
                    'invite_code': 'Invite code is required.'
                })
            invite = OrganisationInvite.objects.select_related(
                'organisation'
            ).filter(code=code).first()

            if not invite or not invite.is_valid():
                raise serializers.ValidationError({
                    'invite_code': 'Invalid or expired invite.'
                })
            if invite.email != attrs['email'].lower().strip():
                raise serializers.ValidationError({
                    'invite_code': 'This invite was sent to a different email.'
                })
            attrs['_invite'] = invite

        return attrs

    def create(self, validated_data):
        with transaction.atomic():
            account_type = validated_data.pop('account_type', 'individual')
            invite       = validated_data.pop('_invite', None)
            org_name     = validated_data.pop('org_name', None)
            org_slug     = validated_data.pop('org_slug', None)
            validated_data.pop('confirm_password', None)
            validated_data.pop('invite_code', None)

            organisation = None
            org_role     = None

            if account_type == 'create_org':
                organisation = Organisation.objects.create(
                    name=org_name,
                    slug=org_slug,
                )
                org_role = User.OrgRole.OWNER

            elif account_type == 'join_org' and invite:
                organisation  = invite.organisation
                org_role      = invite.role
                invite.status = OrganisationInvite.Status.ACCEPTED
                invite.save(update_fields=['status'])

            user = User.objects.create_user(
                organisation=organisation,
                org_role=org_role,
                **validated_data
            )
            return user


#-----------------------Login Serializer-----------------------
class LoginUserSerializer(serializers.Serializer):
    email    = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        return value.lower().strip()

    def validate(self, attrs):
        email    = attrs.get('email')
        password = attrs.get('password')

        user = User.objects.filter(email=email).first()

        if not user:
            raise serializers.ValidationError({
                'email': 'Invalid email or password.'
            })

        if not user.is_active:
            raise serializers.ValidationError({
                'email': 'Your account has been deactivated.'
            })

        if not user.has_usable_password():
            raise serializers.ValidationError({
                'email': 'This account uses Social Login. Please log in with Google.'
            })

        if not user.check_password(password):
            raise serializers.ValidationError({
                'email': 'Invalid email or password.'
            })

        attrs['user'] = user
        return attrs


#-----------------------Organisation Invite Serializer-----------------------
class CreateOrganisationInviteSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role  = serializers.ChoiceField(
        choices=User.OrgRole.choices,
        default=User.OrgRole.MEMBER
    )

    def validate_email(self, value):
        value = value.lower().strip()
        user  = self.context['request'].user
        org   = user.organisation

        if User.objects.filter(email=value, organisation=org).exists():
            raise serializers.ValidationError(
                'This user is already a member of your organisation.'
            )

        if OrganisationInvite.objects.filter(
            email=value,
            organisation=org,
            status=OrganisationInvite.Status.PENDING
        ).exists():
            raise serializers.ValidationError(
                'A pending invite already exists for this email.'
            )

        return value

    def create(self, validated_data):
        user = self.context['request'].user
        return OrganisationInvite.objects.create(
            organisation=user.organisation,
            invited_by=user,
            email=validated_data['email'],
            role=validated_data['role'],
            expires_at=timezone.now() + timedelta(days=7),
        )


#-----------------------Profile Update Serializer-----------------------
class UpdateUserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model  = User
        fields = [
            'first_name', 'last_name', 'phone_number', 'job_title',
            'timezone', 'avatar_url', 'email_notifications', 'meeting_reminders',
        ]


#-----------------------Change Password Serializer-----------------------
class ChangeUserPasswordSerializer(serializers.Serializer):
    old_password     = serializers.CharField(write_only=True)
    new_password     = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Old password is incorrect.')
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({
                'confirm_password': 'Passwords do not match.'
            })
        validate_password(attrs['new_password'])
        return attrs

    def save(self):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save(update_fields=['password', 'updated_at'])
        return user


#-----------------------New Input Serializers-----------------------

class GoogleLoginSerializer(serializers.Serializer):
    token = serializers.CharField()


class RequestPasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower().strip()


class ResetPasswordConfirmSerializer(serializers.Serializer):
    email        = serializers.EmailField()
    otp          = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        return value.lower().strip()
    
    def validate_new_password(self, value):
        validate_password(value)
        return value


class CookieRefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False)


class UpdateOrganisationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Organisation
        fields = ['name', 'slug', 'plan', 'logo_url']