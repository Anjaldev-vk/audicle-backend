from django.urls import path
from . import views

urlpatterns = [
    # Search
    path('search/', views.RAGSearchView.as_view(), name='rag-search'),

    # Chat sessions
    path('chat/sessions/', views.ChatSessionListCreateView.as_view(), name='rag-chat-sessions'),
    path('chat/sessions/<uuid:session_id>/', views.ChatSessionDetailView.as_view(), name='rag-chat-session-detail'),
    path('chat/sessions/<uuid:session_id>/messages/', views.ChatMessageCreateView.as_view(), name='rag-chat-messages'),
]

internal_urlpatterns = [
    path('rag/embed/', views.InternalEmbedView.as_view(), name='internal-rag-embed'),
]