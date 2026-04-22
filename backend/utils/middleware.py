import logging
import time
import uuid

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger("django")


class RequestLoggingMiddleware(MiddlewareMixin):
    """
    Logs every HTTP request and response with:
    - A unique request_id for end-to-end tracing
    - HTTP method and path
    - Response status code
    - Request duration in milliseconds
    - Authenticated user email (or 'anonymous')

    Log levels:
        INFO    → 2xx and 3xx responses
        WARNING → 4xx responses
        ERROR   → 5xx responses and unhandled exceptions
    """

    def process_request(self, request):
        """
        Runs BEFORE the view.
        Stamps request_id and start_time onto the request object.
        """
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
        """
        Runs AFTER the view returns a response.
        Logs status code and total duration.
        Always returns the response — never blocks it.
        """
        request_id  = getattr(request, "request_id", "unknown")
        start_time  = getattr(request, "start_time", time.time())
        duration_ms = int((time.time() - start_time) * 1000)
        status_code = response.status_code
        user        = self._get_user(request)

        # Choose log level based on status code
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

        # Attach request_id to response header
        # Frontend and Nginx can log this for end-to-end tracing
        response["X-Request-ID"] = request_id

        return response

    def process_exception(self, request, exception):
        """
        Runs if the view raises an unhandled exception.
        Logs the full traceback with request context.
        Returning None lets Django continue its normal
        exception handling — does not suppress the error.
        """
        request_id = getattr(request, "request_id", "unknown")

        logger.error(
            "[req=%s] EXCEPTION %s %s user=%s error=%s",
            request_id,
            request.method,
            request.path,
            self._get_user(request),
            str(exception),
            exc_info=True,    # includes full traceback
        )

        return None

    @staticmethod
    def _get_user(request) -> str:
        """
        Safely extract user identifier from the request.
        Returns email if authenticated, 'anonymous' otherwise.
        Wrapped in try/except because this runs before
        authentication middleware on the first pass.
        """
        try:
            if hasattr(request, "user") and request.user.is_authenticated:
                return request.user.email
        except Exception:
            pass
        return "anonymous"