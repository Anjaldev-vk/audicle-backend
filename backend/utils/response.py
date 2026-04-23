from rest_framework.response import Response
from rest_framework import status

def success_response(message, data=None, status_code=status.HTTP_200_OK):
    """
    Standard success response format.
    {
        "success": True,
        "message": "Human readable message.",
        "data": {}
    }
    """
    if data is None:
        data = {}
    return Response(
        {
            "success": True,
            "message": message,
            "data": data,
        },
        status=status_code,
    )

def error_response(message, code, errors=None, status_code=status.HTTP_400_BAD_REQUEST):
    """
    Standard error response format.
    {
        "status": "error",
        "code": "snake_case_code",
        "message": "Human readable message.",
        "errors": {}
    }
    """
    if errors is None:
        errors = {}
    return Response(
        {
            "status": "error",
            "code": code,
            "message": message,
            "errors": errors,
        },
        status=status_code,
    )
