from django.urls import path
from .views import (
    RegisterView, LoginView, LogoutView,
    MeView, ChangePasswordView,
    OrganisationDetailView, OrgMembersView,
    RemoveMemberView, InviteMemberView,
    VerifyInviteView,
    GoogleLoginView,
    CookieRefreshView,          
    RequestPasswordResetView,  
    ResetPasswordConfirmView,
)
from .mfa_views import (
    DisableMFAView,
    EnableMFAView,
    RequestMFARecoveryView,
    VerifyMFARecoveryView,
    VerifyMFATokenView,
    VerifyMFASetupView,
)

urlpatterns = [

    # -------- Authentication & User Management --------
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('login/google/', GoogleLoginView.as_view(), name='google_login'),
    path('logout/', LogoutView.as_view(), name='logout'),

    # ------------------ MFA ------------------
    path("mfa/enable/",          EnableMFAView.as_view(),        name="mfa-enable"),
    path("mfa/verify-setup/",    VerifyMFASetupView.as_view(),   name="mfa-verify-setup"),
    path("mfa/verify/",          VerifyMFATokenView.as_view(),   name="mfa-verify"),
    path("mfa/recover/request/", RequestMFARecoveryView.as_view(), name="mfa-recover-request"),
    path("mfa/recover/verify/",  VerifyMFARecoveryView.as_view(), name="mfa-recover-verify"),
    path("mfa/disable/",         DisableMFAView.as_view(),       name="mfa-disable"),
    
    #------------------ Token Refresh (Cookie-based) ------------------
    path('token/refresh/', CookieRefreshView.as_view(), name='token_refresh'),

    # ------------------ Password Reset ------------------
    path('password-reset/request/', RequestPasswordResetView.as_view(), name='password_reset_request'),
    path('password-reset/confirm/', ResetPasswordConfirmView.as_view(), name='password_reset_confirm'),

    #------------------ Current User Profile ------------------
    path('me/', MeView.as_view(), name='me'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),

    #------------------ Organisation Management ------------------
    path('organisation/', OrganisationDetailView.as_view(), name='organisation'),
    path('organisation/members/', OrgMembersView.as_view(), name='org_members'),
    path('organisation/members/<uuid:user_id>/remove/', RemoveMemberView.as_view(), name='remove_member'),
    path('organisation/invite/', InviteMemberView.as_view(), name='invite_member'),
    path('invite/<str:code>/', VerifyInviteView.as_view(), name='verify_invite'),
]