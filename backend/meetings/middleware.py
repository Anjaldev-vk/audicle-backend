import logging
from urllib.parse import parse_qs
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import UntypedToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.authentication import JWTAuthentication

logger = logging.getLogger("accounts")


@database_sync_to_async
def get_user_from_token(token_key):
    try:
        UntypedToken(token_key)
        jwt_auth = JWTAuthentication()
        validated = jwt_auth.get_validated_token(token_key)
        return jwt_auth.get_user(validated)
    except (InvalidToken, TokenError) as e:
        logger.warning("WebSocket JWT auth failed: %s", str(e))
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        # Token passed as ?token=<jwt> in query string
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        token_list = params.get("token", [])
        if token_list:
            scope["user"] = await get_user_from_token(token_list[0])
        else:
            scope["user"] = AnonymousUser()
        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
