import logging
import time
import uuid

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger("django")


class RequestLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.request_id = str(uuid.uuid4())[:8]
        request.start_time = time.time()

        logger.info(
            "[req=%s] START %s %s user=%s",
            request.request_id,
            request.method,
            request.path,
            self._get_user(request),
        )

    def process_response(self, request, response):
        request_id  = getattr(request, "request_id", "unknown")
        start_time  = getattr(request, "start_time", time.time())
        duration_ms = int((time.time() - start_time) * 1000)
        status_code = response.status_code
        user        = self._get_user(request)

        if status_code >= 500:
            log_fn = logger.error
        elif status_code >= 400:
            log_fn = logger.warning
        else:
            log_fn = logger.info

        log_fn(
            "[req=%s] END %s %s status=%d duration=%dms user=%s",
            request_id,
            request.method,
            request.path,
            status_code,
            duration_ms,
            user,
        )

        response["X-Request-ID"] = request_id
        return response

    def process_exception(self, request, exception):
        request_id = getattr(request, "request_id", "unknown")
        logger.error(
            "[req=%s] EXCEPTION %s %s user=%s error=%s",
            request_id,
            request.method,
            request.path,
            self._get_user(request),
            str(exception),
            exc_info=True,
        )
        return None

    @staticmethod
    def _get_user(request) -> str:
        try:
            if hasattr(request, "user") and request.user.is_authenticated:
                return request.user.email
        except Exception:
            pass
        return "anonymous"


class WorkspaceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _workspace_error(message, code, status_code):
        from django.http import JsonResponse
        return JsonResponse(
            {
                "success": False,
                "status": "error",
                "code": code,
                "message": message,
                "errors": {},
            },
            status=status_code,
        )

    def __call__(self, request):
        # Exempt authentication and public endpoints from workspace checks
        exempt_paths = [
            '/api/v1/accounts/login/',
            '/api/v1/accounts/register/',
            '/api/v1/accounts/password-reset/',
            '/api/v1/accounts/token/refresh/',
            '/api/v1/accounts/invite/',
            '/api/v1/accounts/mfa/',
            '/api/v1/accounts/verify/',
        ]
        
        if any(request.path.startswith(path) for path in exempt_paths):
            return self.get_response(request)

        request.organisation = None
        request.membership = None
        request.workspace_type = 'personal'

        # 1. Resolve User
        user = getattr(request, 'user', None)
        
        # Support for DRF force_authenticate in tests
        if not (user and user.is_authenticated):
            if hasattr(request, '_force_auth_user'):
                request.user = request._force_auth_user
                user = request.user

        if not (user and user.is_authenticated):
            # Try manual JWT authentication for API requests
            from rest_framework_simplejwt.authentication import JWTAuthentication
            try:
                auth = JWTAuthentication().authenticate(request)
                if auth:
                    request.user = auth[0]
                    user = request.user
            except Exception:
                pass

        # 2. Resolve Workspace if authenticated
        if user and user.is_authenticated:
            workspace_id = request.headers.get('X-Workspace-ID', 'personal')
            
            if workspace_id and workspace_id != 'personal':
                try:
                    # Ensure we have a valid UUID before querying
                    try:
                        val = uuid.UUID(str(workspace_id))
                    except ValueError:
                        logger.warning("[workspace] Invalid UUID format: %s", workspace_id)
                        return self._workspace_error(
                            message="Invalid workspace ID",
                            code="invalid_workspace",
                            status_code=403,
                        )

                    try:
                        from accounts.models import Membership
                        membership = Membership.objects.select_related(
                            'organisation'
                        ).get(
                            user=user,
                            organisation_id=val
                        )
                        request.organisation = membership.organisation
                        request.membership = membership
                        request.workspace_type = 'organisation'
                    except Membership.DoesNotExist:
                        logger.warning(
                            "[workspace] Unauthorized access attempt to workspace %s by user %s",
                            workspace_id, user.email
                        )
                        return self._workspace_error(
                            message="Unauthorized workspace access",
                            code="unauthorized_workspace",
                            status_code=403,
                        )
                except Exception as e:
                    logger.error("[workspace] Critical error resolving workspace %s: %s", workspace_id, str(e))
                    return self._workspace_error(
                        message="Error resolving workspace",
                        code="workspace_resolution_error",
                        status_code=500,
                    )



        return self.get_response(request)
