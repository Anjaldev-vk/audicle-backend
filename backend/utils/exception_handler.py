from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
import logging

logger = logging.getLogger(__name__)

def custom_exception_handler(exc, context):
    """
    Custom exception handler to standardize error responses.
    """
    # Call DRF's default exception handler first to get the standard error response.
    response = exception_handler(exc, context)

    if response is not None:
        # Standardize the data from the default handler
        custom_data = {
            "status": "error",
            "message": "Validation or client error occurred.",
            "code": "client_error",
            "errors": {}
        }

        # Handling different status codes
        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            custom_data["message"] = "Authentication credentials were not provided or are invalid."
            custom_data["code"] = "unauthorized"
        elif response.status_code == status.HTTP_403_FORBIDDEN:
            custom_data["message"] = "You do not have permission to perform this action."
            custom_data["code"] = "permission_denied"
        elif response.status_code == status.HTTP_404_NOT_FOUND:
            custom_data["message"] = "The requested resource was not found."
            custom_data["code"] = "not_found"
        elif response.status_code == status.HTTP_400_BAD_REQUEST:
            custom_data["message"] = "A validation error occurred."
            custom_data["code"] = "validation_error"
            # Extract the raw errors from DRF
            custom_data["errors"] = response.data

        response.data = custom_data

    else:
        # This handles errors that DRF doesn't catch by default (like database or server crashes)
        # 500 Internal Server Error
        logger.error(f"Unhandled Exception: {exc}", exc_info=True)
        
        response = Response({
            "status": "error",
            "message": "An unexpected server error occurred. Our team has been notified.",
            "code": "internal_server_error",
            "errors": None
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return response
