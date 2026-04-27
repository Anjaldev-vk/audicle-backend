import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organisation, User
from meetings.models import Meeting
from rag.models import ChatMessage, ChatSession, EmbeddingChunk
from transcripts.models import Transcript


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def org():
    return Organisation.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def user(org):
    return User.objects.create_user(
        email="rag@test.com",
        password="testpass123",
        first_name="Test",
        last_name="User",
        organisation=org,
        org_role="member",
        is_verified=True,
    )


@pytest.fixture
def individual_user():
    return User.objects.create_user(
        email="individual@test.com",
        password="testpass123",
        first_name="Individual",
        last_name="User",
        is_verified=True,
    )


@pytest.fixture
def meeting(org, user):
    return Meeting.objects.create(
        title="Test Meeting",
        platform=Meeting.Platform.UPLOAD,
        status=Meeting.Status.COMPLETED,
        organisation=org,
        created_by=user,
    )


@pytest.fixture
def transcript(meeting, org, user):
    return Transcript.objects.create(
        meeting=meeting,
        organisation=org,
        created_by=user,
        status=Transcript.Status.COMPLETED,
        raw_text="This is a test transcript about project planning.",
        language="en",
    )


@pytest.fixture
def embedding_chunk(transcript, meeting, org, user):
    return EmbeddingChunk.objects.create(
        transcript=transcript,
        meeting=meeting,
        organisation=org,
        created_by=user,
        chunk_text="This is a test transcript about project planning.",
        embedding=[0.1] * 768,
        chunk_index=0,
        start_seconds=0.0,
        end_seconds=10.0,
    )


@pytest.fixture
def chat_session(user, org):
    return ChatSession.objects.create(
        user=user,
        organisation=org,
        title="Test Session",
    )


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def individual_auth_client(individual_user):
    client = APIClient()
    client.force_authenticate(user=individual_user)
    return client


# ── EmbeddingChunk model tests ────────────────────────────────────────────────

@pytest.mark.django_db
class TestEmbeddingChunkModel:

    def test_create_embedding_chunk(self, transcript, meeting, org, user):
        chunk = EmbeddingChunk.objects.create(
            transcript=transcript,
            meeting=meeting,
            organisation=org,
            created_by=user,
            chunk_text="Hello world",
            embedding=[0.1] * 768,
            chunk_index=0,
        )
        assert chunk.id is not None
        assert chunk.chunk_text == "Hello world"
        assert chunk.chunk_index == 0
        assert chunk.start_seconds is None
        assert chunk.end_seconds is None

    def test_embedding_chunk_str(self, embedding_chunk, meeting):
        assert str(meeting.title) in str(embedding_chunk)

    def test_embedding_chunk_ordering(self, transcript, meeting, org, user):
        EmbeddingChunk.objects.create(
            transcript=transcript, meeting=meeting,
            organisation=org, created_by=user,
            chunk_text="Second", embedding=[0.2] * 768, chunk_index=1,
        )
        EmbeddingChunk.objects.create(
            transcript=transcript, meeting=meeting,
            organisation=org, created_by=user,
            chunk_text="First", embedding=[0.1] * 768, chunk_index=0,
        )
        chunks = list(EmbeddingChunk.objects.filter(transcript=transcript))
        assert chunks[0].chunk_index == 0
        assert chunks[1].chunk_index == 1

    def test_chunk_deleted_when_transcript_deleted(self, embedding_chunk, transcript):
        transcript.delete()
        assert EmbeddingChunk.objects.filter(id=embedding_chunk.id).count() == 0


# ── ChatSession model tests ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestChatSessionModel:

    def test_create_chat_session(self, user, org):
        session = ChatSession.objects.create(
            user=user,
            organisation=org,
            title="Planning discussion",
        )
        assert session.id is not None
        assert session.title == "Planning discussion"
        assert session.user == user

    def test_chat_session_str(self, chat_session, user):
        assert user.email in str(chat_session)

    def test_chat_session_ordering(self, user, org):
        s1 = ChatSession.objects.create(user=user, organisation=org)
        s2 = ChatSession.objects.create(user=user, organisation=org)
        sessions = list(ChatSession.objects.filter(user=user))
        # Most recently updated first
        assert sessions[0].id == s2.id


# ── ChatMessage model tests ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestChatMessageModel:

    def test_create_user_message(self, chat_session):
        msg = ChatMessage.objects.create(
            session=chat_session,
            role=ChatMessage.Role.USER,
            content="What was discussed?",
        )
        assert msg.role == "user"
        assert msg.sources == []

    def test_create_assistant_message(self, chat_session, meeting):
        msg = ChatMessage.objects.create(
            session=chat_session,
            role=ChatMessage.Role.ASSISTANT,
            content="The meeting discussed project planning.",
            sources=[str(meeting.id)],
        )
        assert msg.role == "assistant"
        assert len(msg.sources) == 1

    def test_message_deleted_when_session_deleted(self, chat_session):
        msg = ChatMessage.objects.create(
            session=chat_session,
            role=ChatMessage.Role.USER,
            content="Test",
        )
        chat_session.delete()
        assert ChatMessage.objects.filter(id=msg.id).count() == 0


# ── RAG Search endpoint tests ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestRAGSearchView:

    def test_search_requires_auth(self):
        client = APIClient()
        response = client.post('/api/v1/rag/search/', {'query': 'test'})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_search_missing_query(self, auth_client):
        response = auth_client.post('/api/v1/rag/search/', {})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_search_query_too_long(self, auth_client):
        response = auth_client.post('/api/v1/rag/search/', {
            'query': 'x' * 1001
        })
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch('rag.views.get_ai_provider')
    def test_search_no_chunks_returns_empty(self, mock_ai, auth_client):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1] * 768
        mock_ai.return_value = mock_provider

        response = auth_client.post('/api/v1/rag/search/', {
            'query': 'What was discussed?'
        })
        assert response.status_code == status.HTTP_200_OK
        assert 'answer' in response.data['data']
        assert response.data['data']['sources'] == []

    @patch('rag.views.get_ai_provider')
    def test_search_with_chunks_returns_answer(
        self, mock_ai, auth_client, embedding_chunk
    ):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1] * 768
        mock_provider.complete.return_value = "The meeting discussed project planning."
        mock_ai.return_value = mock_provider

        response = auth_client.post('/api/v1/rag/search/', {
            'query': 'What was discussed?'
        })
        assert response.status_code == status.HTTP_200_OK
        data = response.data['data']
        assert 'answer' in data
        assert 'sources' in data
        assert 'chunks_used' in data

    @patch('rag.views.get_ai_provider')
    def test_search_scoped_to_meeting(
        self, mock_ai, auth_client, embedding_chunk, meeting
    ):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1] * 768
        mock_provider.complete.return_value = "Answer."
        mock_ai.return_value = mock_provider

        response = auth_client.post('/api/v1/rag/search/', {
            'query': 'test',
            'meeting_id': str(meeting.id),
        })
        assert response.status_code == status.HTTP_200_OK

    @patch('rag.views.get_ai_provider')
    def test_search_embedding_failure_returns_503(self, mock_ai, auth_client):
        mock_provider = MagicMock()
        mock_provider.embed.side_effect = Exception("Embedding failed")
        mock_ai.return_value = mock_provider

        response = auth_client.post('/api/v1/rag/search/', {
            'query': 'What was discussed?'
        })
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    @patch('rag.views.get_ai_provider')
    def test_search_tenant_isolation(
        self, mock_ai, auth_client, individual_auth_client, embedding_chunk
    ):
        """Individual user cannot see org user's chunks."""
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1] * 768
        mock_provider.complete.return_value = "Answer."
        mock_ai.return_value = mock_provider

        response = individual_auth_client.post('/api/v1/rag/search/', {
            'query': 'project planning'
        })
        assert response.status_code == status.HTTP_200_OK
        # Individual user gets no chunks — different tenant
        assert response.data['data']['sources'] == []


# ── Chat Session endpoint tests ───────────────────────────────────────────────

@pytest.mark.django_db
class TestChatSessionListCreateView:

    def test_list_sessions_requires_auth(self):
        client = APIClient()
        response = client.get('/api/v1/rag/chat/sessions/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_sessions_empty(self, auth_client):
        response = auth_client.get('/api/v1/rag/chat/sessions/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['data'] == []

    def test_list_sessions_returns_own_sessions(self, auth_client, chat_session):
        response = auth_client.get('/api/v1/rag/chat/sessions/')
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['data']) == 1
        assert response.data['data'][0]['id'] == str(chat_session.id)

    def test_create_session(self, auth_client):
        response = auth_client.post('/api/v1/rag/chat/sessions/')
        assert response.status_code == status.HTTP_201_CREATED
        assert 'id' in response.data['data']

    def test_create_session_sets_organisation(self, auth_client, user):
        response = auth_client.post('/api/v1/rag/chat/sessions/')
        assert response.status_code == status.HTTP_201_CREATED
        session = ChatSession.objects.get(id=response.data['data']['id'])
        assert session.organisation == user.organisation

    def test_sessions_isolated_between_users(
        self, auth_client, individual_auth_client, chat_session
    ):
        response = individual_auth_client.get('/api/v1/rag/chat/sessions/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['data'] == []


# ── Chat Session Detail endpoint tests ───────────────────────────────────────

@pytest.mark.django_db
class TestChatSessionDetailView:

    def test_get_session(self, auth_client, chat_session):
        response = auth_client.get(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['data']['id'] == str(chat_session.id)

    def test_get_session_not_found(self, auth_client):
        response = auth_client.get(
            f'/api/v1/rag/chat/sessions/{uuid.uuid4()}/'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_session_wrong_user(self, individual_auth_client, chat_session):
        response = individual_auth_client.get(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_session(self, auth_client, chat_session):
        response = auth_client.delete(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/'
        )
        assert response.status_code == status.HTTP_200_OK
        assert ChatSession.objects.filter(id=chat_session.id).count() == 0

    def test_delete_session_wrong_user(self, individual_auth_client, chat_session):
        response = individual_auth_client.delete(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert ChatSession.objects.filter(id=chat_session.id).count() == 1


# ── Chat Message endpoint tests ───────────────────────────────────────────────

@pytest.mark.django_db
class TestChatMessageCreateView:

    def test_send_message_requires_auth(self, chat_session):
        client = APIClient()
        response = client.post(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/messages/',
            {'content': 'Hello'}
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_send_message_missing_content(self, auth_client, chat_session):
        response = auth_client.post(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/messages/',
            {}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_send_message_wrong_session(self, individual_auth_client, chat_session):
        response = individual_auth_client.post(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/messages/',
            {'content': 'Hello'}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch('rag.views.get_ai_provider')
    def test_send_message_creates_user_and_assistant_messages(
        self, mock_ai, auth_client, chat_session
    ):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1] * 768
        mock_provider.complete.return_value = "Here is my answer."
        mock_ai.return_value = mock_provider

        response = auth_client.post(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/messages/',
            {'content': 'What was discussed?'}
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.data['data']
        assert data['user_message']['role'] == 'user'
        assert data['assistant_message']['role'] == 'assistant'
        assert data['assistant_message']['content'] == "Here is my answer."

    @patch('rag.views.get_ai_provider')
    def test_first_message_sets_session_title(
        self, mock_ai, auth_client, user, org
    ):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1] * 768
        mock_provider.complete.return_value = "Answer."
        mock_ai.return_value = mock_provider

        session = ChatSession.objects.create(user=user, organisation=org)
        response = auth_client.post(
            f'/api/v1/rag/chat/sessions/{session.id}/messages/',
            {'content': 'What did we decide about the budget?'}
        )
        assert response.status_code == status.HTTP_201_CREATED
        session.refresh_from_db()
        assert session.title == 'What did we decide about the budget?'

    @patch('rag.views.get_ai_provider')
    def test_message_title_truncated_at_80_chars(
        self, mock_ai, auth_client, user, org
    ):
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1] * 768
        mock_provider.complete.return_value = "Answer."
        mock_ai.return_value = mock_provider

        session = ChatSession.objects.create(user=user, organisation=org)
        long_content = 'x' * 200
        auth_client.post(
            f'/api/v1/rag/chat/sessions/{session.id}/messages/',
            {'content': long_content}
        )
        session.refresh_from_db()
        assert len(session.title) == 80

    @patch('rag.views.get_ai_provider')
    def test_send_message_embedding_failure_returns_503(
        self, mock_ai, auth_client, chat_session
    ):
        mock_provider = MagicMock()
        mock_provider.embed.side_effect = Exception("Embedding failed")
        mock_ai.return_value = mock_provider

        response = auth_client.post(
            f'/api/v1/rag/chat/sessions/{chat_session.id}/messages/',
            {'content': 'What was discussed?'}
        )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# ── Internal embed endpoint tests ─────────────────────────────────────────────

@pytest.mark.django_db
class TestInternalEmbedView:

    def test_rejects_missing_secret(self, transcript):
        client = APIClient()
        response = client.post('/internal/rag/embed/', {
            'transcript_id': str(transcript.id),
            'chunks': [],
        })
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_rejects_wrong_secret(self, transcript):
        client = APIClient()
        response = client.post(
            '/internal/rag/embed/',
            {'transcript_id': str(transcript.id), 'chunks': []},
            HTTP_X_INTERNAL_SECRET='wrong-secret',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_stores_embedding_chunks(self, transcript, settings):
        client = APIClient()
        chunks = [
            {
                'chunk_text':    'First chunk of text',
                'chunk_index':   0,
                'start_seconds': 0.0,
                'end_seconds':   10.0,
                'embedding':     [0.1] * 768,
            },
            {
                'chunk_text':    'Second chunk of text',
                'chunk_index':   1,
                'start_seconds': 10.0,
                'end_seconds':   20.0,
                'embedding':     [0.2] * 768,
            },
        ]
        response = client.post(
            '/internal/rag/embed/',
            {'transcript_id': str(transcript.id), 'chunks': chunks},
            format='json',
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['data']['chunks_stored'] == 2
        assert EmbeddingChunk.objects.filter(transcript=transcript).count() == 2

    def test_replaces_old_chunks_on_retry(self, transcript, settings):
        """Calling embed twice replaces old chunks — no duplicates."""
        client = APIClient()
        chunk_data = [{
            'chunk_text':  'Chunk',
            'chunk_index': 0,
            'embedding':   [0.1] * 768,
        }]
        payload = {'transcript_id': str(transcript.id), 'chunks': chunk_data}

        client.post(
            '/internal/rag/embed/', payload, format='json',
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        client.post(
            '/internal/rag/embed/', payload, format='json',
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )

        assert EmbeddingChunk.objects.filter(transcript=transcript).count() == 1

    def test_returns_404_for_unknown_transcript(self, settings):
        client = APIClient()
        response = client.post(
            '/internal/rag/embed/',
            {'transcript_id': str(uuid.uuid4()), 'chunks': []},
            format='json',
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND