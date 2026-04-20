from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, Organisation, OrganisationInvite, ApiKey


# ──────────────────────────────────────────────
# Organisation
# ──────────────────────────────────────────────
@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display  = ('name', 'slug', 'plan', 'meetings_this_month', 'created_at')
    list_filter   = ('plan',)
    search_fields = ('name', 'slug')
    readonly_fields = ('id', 'created_at', 'updated_at')
    ordering      = ('name',)


# ──────────────────────────────────────────────
# User
# ──────────────────────────────────────────────
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # Columns shown in the list view
    list_display  = ('email', 'full_name', 'organisation', 'org_role',
                     'is_verified', 'is_active', 'is_staff', 'created_at')
    list_filter   = ('is_active', 'is_verified', 'is_staff', 'org_role')
    search_fields = ('email', 'first_name', 'last_name')
    ordering      = ('-created_at',)
    readonly_fields = ('id', 'created_at', 'updated_at', 'last_login')

    # Fields shown on the detail / edit page
    fieldsets = (
        (None, {
            'fields': ('id', 'email', 'password')
        }),
        ('Personal Info', {
            'fields': ('first_name', 'last_name', 'phone_number',
                       'job_title', 'timezone', 'avatar_url')
        }),
        ('Organisation', {
            'fields': ('organisation', 'org_role')
        }),
        ('Preferences', {
            'fields': ('email_notifications', 'meeting_reminders')
        }),
        ('Status & Verification', {
            'fields': ('is_active', 'is_verified', 'is_staff',
                       'otp', 'otp_expiry')
        }),
        ('Permissions', {
            'fields': ('is_superuser', 'groups', 'user_permissions'),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'last_login'),
            'classes': ('collapse',),
        }),
    )

    # Fields shown when creating a new user via admin
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name',
                       'password1', 'password2',
                       'organisation', 'org_role',
                       'is_active', 'is_staff'),
        }),
    )


# ──────────────────────────────────────────────
# OrganisationInvite
# ──────────────────────────────────────────────
@admin.register(OrganisationInvite)
class OrganisationInviteAdmin(admin.ModelAdmin):
    list_display  = ('email', 'organisation', 'role', 'status',
                     'invited_by', 'expires_at', 'created_at')
    list_filter   = ('status', 'role')
    search_fields = ('email', 'organisation__name', 'code')
    readonly_fields = ('id', 'code', 'created_at', 'updated_at')
    ordering      = ('-created_at',)


# ──────────────────────────────────────────────
# ApiKey
# ──────────────────────────────────────────────
@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display  = ('prefix', 'name', 'user', 'is_active',
                     'last_used_at', 'expires_at', 'created_at')
    list_filter   = ('is_active',)
    search_fields = ('prefix', 'name', 'user__email')
    # Never expose the raw hash — mark it read-only
    readonly_fields = ('id', 'key_hash', 'prefix', 'created_at', 'updated_at', 'last_used_at')
    ordering      = ('-created_at',)
