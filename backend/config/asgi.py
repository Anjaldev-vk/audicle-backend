import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing consumers and routing.
django_asgi_app = get_asgi_application()

from meetings.routing import websocket_urlpatterns
from meetings.middleware import JWTAuthMiddlewareStack
from django.urls import path
from notifications.consumers import NotificationConsumer

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddlewareStack(
            URLRouter(
                websocket_urlpatterns + [
                    path('ws/v1/notifications/', NotificationConsumer.as_asgi()),
                ]
            )
        ),
    }
)
