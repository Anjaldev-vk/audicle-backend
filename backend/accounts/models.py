import uuid
import secrets
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils import timezone
from django.core.exceptions import ValidationError
from .managers import UserManager


# -------------------BaseModel-------------------
class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# --------------------Organisation--------------------
class Organisation(BaseModel):

    class Plan(models.TextChoices):
        FREE = 'free', 'Free'
        PRO = 'pro', 'Pro'
        ENTERPRISE = 'enterprise', 'Enterprise'

    name = models.CharField(max_length=255)
    slug = models.SlugField(
        unique=True,
        db_index=True,
        help_text='URL-safe unique identifier for the organisation.'
    )
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.FREE,
        help_text='Current subscription plan.'
    )
    logo_url = models.URLField(max_length=500, blank=True)

    #-------- Usage tracking — used for plan limit enforcement later --------
    meetings_this_month = models.IntegerField(default=0)
    usage_reset_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date when monthly usage counters were last reset.'
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Organisation'
        verbose_name_plural = 'Organisations'

    def __str__(self):
        return self.name


# --------------------User--------------------
class User(AbstractBaseUser, PermissionsMixin):

    class OrgRole(models.TextChoices):
        OWNER = 'owner', 'Owner'
        ADMIN = 'admin', 'Admin'
        MEMBER = 'member', 'Member'

    # Manual field definition — avoids MRO conflict
    # with PermissionsMixin when using BaseModel
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Multi-tenancy — null means individual user
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.SET_NULL,  # user survives if org is deleted
        null=True,
        blank=True,
        related_name='users',
        db_index=True,
        help_text='Null = individual user. Set = organisation member.'
    )
    org_role = models.CharField(
        max_length=20,
        choices=OrgRole.choices,
        null=True,
        blank=True,
        db_index=True,
        help_text='Must be set when organisation is set. Must be null when organisation is null.'
    )

    # Core authentication
    email = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)

    # Profile
    phone_number = models.CharField(max_length=20, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    timezone = models.CharField(max_length=50, default='UTC')
    avatar_url = models.URLField(max_length=500, blank=True)

    # Preferences
    email_notifications = models.BooleanField(default=True)
    meeting_reminders = models.BooleanField(default=True)

    # Status flags
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)

    otp = models.CharField(
        max_length=6, 
        null=True, 
        blank=True,
        help_text='Hashed or plain numeric code for password resets/verification.'
    )
    otp_expiry = models.DateTimeField(
        null=True, 
        blank=True,
        help_text='Timestamp after which the current OTP becomes invalid.'
    )

    # MFA
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret  = models.CharField(max_length=32, null=True, blank=True)

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    objects = UserManager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        indexes = [
            models.Index(
                fields=['organisation', 'is_active'],
                name='user_org_active_idx'
            ),
            models.Index(
                fields=['organisation', 'org_role'],
                name='user_org_role_idx'
            ),
        ]

    def __str__(self):
        return self.email

    def clean(self):
        """
        Hard enforcement of hybrid identity logic.
        Prevents orphaned roles and role-less members.
        Triggered via full_clean() in UserManager._create_user().
        """
        super().clean()

        # individual user cannot have a role
        if self.organisation is None and self.org_role is not None:
            raise ValidationError({
                'org_role': 'Individual users cannot have an organisation role.'
            })

        # org member must have a role
        if self.organisation is not None and self.org_role is None:
            raise ValidationError({
                'org_role': 'Users in an organisation must have a role.'
            })

    # ── properties ──────────────────────────────

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def is_individual(self):
        """True if user has no organisation — freelancer or solo user."""
        return self.organisation is None

    @property
    def is_org_member(self):
        """True if user belongs to any organisation."""
        return self.organisation is not None

    @property
    def is_org_admin(self):
        """True if user is owner or admin of their organisation."""
        return self.organisation is not None and \
               self.org_role in [self.OrgRole.OWNER, self.OrgRole.ADMIN]


# ─────────────────────────────────────────────
# OrganisationInvite
# Tracks pending invitations to join an org.
# Invite is valid only when status=pending
# and the expiry has not passed.
# ─────────────────────────────────────────────
class OrganisationInvite(BaseModel):

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACCEPTED = 'accepted', 'Accepted'
        EXPIRED = 'expired', 'Expired'

    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name='invites'
    )
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='sent_invites'
    )
    email = models.EmailField(
        db_index=True,
        help_text='Email address of the person being invited.'
    )
    role = models.CharField(
        max_length=20,
        choices=User.OrgRole.choices,
        default=User.OrgRole.MEMBER,
        help_text='Role the invited user will receive on joining.'
    )
    code = models.CharField(
        max_length=64,
        unique=True,
        default=secrets.token_urlsafe,
        help_text='URL-safe token sent to the invitee. Never expose in list endpoints.'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    expires_at = models.DateTimeField(
        help_text='Invite automatically becomes invalid after this time.'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Organisation Invite'
        verbose_name_plural = 'Organisation Invites'
        indexes = [
            models.Index(
                fields=['organisation', 'status'],
                name='invite_org_status_idx'
            ),
        ]

    def __str__(self):
        return f'Invite → {self.email} ({self.organisation.name})'

    def is_valid(self):
        """
        Returns True only if the invite is pending
        and has not passed its expiry date.
        """
        return (
            self.status == self.Status.PENDING and
            timezone.now() < self.expires_at
        )


# ─────────────────────────────────────────────
# ApiKey
# External integration keys.
# Rule: never store the raw key — hash only.
# prefix is shown in the UI so users can
# identify which key is which.
# ─────────────────────────────────────────────
class ApiKey(BaseModel):

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='api_keys'
    )
    key_hash = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text='SHA-256 hash of the raw key. Never store the raw key.'
    )
    name = models.CharField(
        max_length=100,
        help_text='Human readable label e.g. "Production Bot Key".'
    )
    prefix = models.CharField(
        max_length=8,
        help_text='First 8 chars of the raw key shown in UI e.g. "re_live_".'
    )
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Updated every time this key is used to authenticate.'
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Optional expiry. Null means the key never expires.'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'API Key'
        verbose_name_plural = 'API Keys'

    def __str__(self):
        return f'{self.prefix}... — {self.user.email}'

    def is_expired(self):
        """Returns True if the key has an expiry date that has passed."""
        if self.expires_at is None:
            return False
        return timezone.now() > self.expires_at

    def is_usable(self):
        """Returns True if the key is active and not expired."""
        return self.is_active and not self.is_expired()