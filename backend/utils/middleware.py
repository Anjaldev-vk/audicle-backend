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

    def __call__(self, request):
        request.organisation = None
        request.membership = None
        request.workspace_type = 'personal'

        # 1. Resolve User (Django session or JWT)
        user = getattr(request, 'user', None)
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
                    from accounts.models import Membership
                    membership = Membership.objects.select_related(
                        'organisation'
                    ).get(
                        user=user,
                        organisation_id=workspace_id
                    )
                    request.organisation = membership.organisation
                    request.membership = membership
                    request.workspace_type = 'organisation'
                except Exception:
                    pass

        return self.get_response(request)