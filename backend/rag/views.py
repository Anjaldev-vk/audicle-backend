import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from transcripts.models import Transcript
from utils.response import success_response, error_response
from utils.ai_client import get_ai_provider
from .models import EmbeddingChunk, ChatSession, ChatMessage
from .serializers import (
    RAGSearchSerializer,
    ChatSessionSerializer,
    ChatSessionListSerializer,
    ChatMessageSerializer,
    ChatMessageCreateSerializer,
    InternalEmbedSerializer,
)
from .utils import (
    search_similar_chunks,
    build_context_from_chunks,
    build_search_prompt,
    build_chat_prompt,
)
from utils.permissions import IsInternalService

logger = logging.getLogger('rag')


class RAGSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RAGSearchSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code='validation_error',
                message='Invalid search parameters',
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        query = serializer.validated_data['query']
        meeting_id = serializer.validated_data.get('meeting_id')
        limit = serializer.validated_data.get('limit', 5)

        try:
            ai = get_ai_provider()
            query_embedding = ai.embed(query)
        except Exception as e:
            logger.error('Embedding failed for user %s: %s', request.user.id, e)
            return error_response(
                code='embedding_error',
                message='Failed to embed search query',
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        chunks = search_similar_chunks(
            user=request.user,
            query_embedding=query_embedding,
            meeting_id=meeting_id,
            limit=limit
        )

        if not chunks:
            return success_response(
                message='No relevant content found',
                data={'answer': 'I could not find relevant information in the meeting transcripts.', 'sources': []}
            )

        context = build_context_from_chunks(chunks)
        prompt = build_search_prompt(query, context)

        try:
            answer = ai.complete(prompt)
        except Exception as e:
            logger.error('AI completion failed for user %s: %s', request.user.id, e)
            return error_response(
                code='ai_error',
                message='Failed to generate answer',
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        sources = list({str(chunk.meeting_id) for chunk in chunks})

        logger.info('RAG search completed for user %s', request.user.id)

        return success_response(
            message='Search completed',
            data={
                'answer': answer,
                'sources': sources,
                'chunks_used': len(chunks)
            }
        )


class ChatSessionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.organisation:
            sessions = ChatSession.objects.filter(
                user=request.user
            ).prefetch_related('messages')
        else:
            sessions = ChatSession.objects.filter(
                user=request.user
            ).prefetch_related('messages')

        serializer = ChatSessionListSerializer(sessions, many=True)
        return success_response(
            message='Chat sessions retrieved',
            data=serializer.data
        )

    def post(self, request):
        with transaction.atomic():
            session = ChatSession.objects.create(
                user=request.user,
                organisation=request.user.organisation
            )

        logger.info('Chat session created for user %s', request.user.id)

        serializer = ChatSessionSerializer(session)
        return success_response(
            message='Chat session created',
            data=serializer.data,
            status_code=status.HTTP_201_CREATED
        )


class ChatSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, session_id, user):
        try:
            return ChatSession.objects.prefetch_related('messages').get(
                id=session_id,
                user=user
            )
        except ChatSession.DoesNotExist:
            return None

    def get(self, request, session_id):
        session = self.get_object(session_id, request.user)
        if not session:
            return error_response(
                code='not_found',
                message='Chat session not found',
                status_code=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatSessionSerializer(session)
        return success_response(
            message='Chat session retrieved',
            data=serializer.data
        )

    def delete(self, request, session_id):
        session = self.get_object(session_id, request.user)
        if not session:
            return error_response(
                code='not_found',
                message='Chat session not found',
                status_code=status.HTTP_404_NOT_FOUND
            )

        session.delete()
        logger.info(
            'Chat session %s deleted by user %s',
            session_id,
            request.user.id
        )

        return success_response(
            message='Chat session deleted',
            data={},
            status_code=status.HTTP_200_OK
        )


class ChatMessageCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get_session(self, session_id, user):
        try:
            return ChatSession.objects.prefetch_related('messages').get(
                id=session_id,
                user=user
            )
        except ChatSession.DoesNotExist:
            return None

    def post(self, request, session_id):
        session = self.get_session(session_id, request.user)
        if not session:
            return error_response(
                code='not_found',
                message='Chat session not found',
                status_code=status.HTTP_404_NOT_FOUND
            )

        serializer = ChatMessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code='validation_error',
                message='Invalid message',
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user_content = serializer.validated_data['content']
        meeting_id = serializer.validated_data.get('meeting_id')

        # Save user message
        user_message = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.Role.USER,
            content=user_content
        )

        # Auto-title session from first message
        if ChatMessage.objects.filter(session=session).count() == 1:
            session.title = user_content[:80]
            session.save(update_fields=['title', 'updated_at'])

        # Embed the query
        try:
            ai = get_ai_provider()
            query_embedding = ai.embed(user_content)
        except Exception as e:
            logger.error(
                'Embedding failed in chat for user %s: %s',
                request.user.id, e
            )
            return error_response(
                code='embedding_error',
                message='Failed to process message',
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        # Retrieve relevant chunks
        chunks = search_similar_chunks(
            user=request.user,
            query_embedding=query_embedding,
            meeting_id=meeting_id,
            limit=5
        )

        # Build prompt with history (last 10 messages for context window)
        history = list(session.messages.exclude(id=user_message.id).order_by('-created_at')[:10])[::-1]
        context = build_context_from_chunks(chunks) if chunks else 'No relevant meeting content found.'
        prompt = build_chat_prompt(user_content, context, history)

        # Generate AI response
        try:
            answer = ai.complete(prompt)
        except Exception as e:
            logger.error(
                'AI completion failed in chat for user %s: %s',
                request.user.id, e
            )
            return error_response(
                code='ai_error',
                message='Failed to generate response',
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        sources = list({str(chunk.meeting_id) for chunk in chunks})

        # Save assistant message
        assistant_message = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.Role.ASSISTANT,
            content=answer,
            sources=sources
        )

        session.save(update_fields=['updated_at'])

        logger.info(
            'Chat message processed for session %s user %s',
            session_id,
            request.user.id
        )

        return success_response(
            message='Message sent',
            data={
                'user_message': ChatMessageSerializer(user_message).data,
                'assistant_message': ChatMessageSerializer(assistant_message).data,
                'sources': sources
            },
            status_code=status.HTTP_201_CREATED
        )


class InternalEmbedView(APIView):
    """
    Internal endpoint — called by ai_worker only.
    Authenticated via X-Internal-Secret header, not JWT.
    """
    authentication_classes = []
    permission_classes = [IsInternalService]

    def post(self, request):
        serializer = InternalEmbedSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code='validation_error',
                message='Invalid embed payload',
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        transcript_id = serializer.validated_data['transcript_id']
        chunks_data = serializer.validated_data['chunks']

        try:
            transcript = Transcript.objects.select_related(
                'meeting', 'organisation', 'created_by'
            ).get(id=transcript_id)
        except Transcript.DoesNotExist:
            return error_response(
                code='not_found',
                message='Transcript not found',
                status_code=status.HTTP_404_NOT_FOUND
            )

        try:
            with transaction.atomic():
                # Clear old chunks for this transcript
                EmbeddingChunk.objects.filter(transcript=transcript).delete()

                chunks_to_create = [
                    EmbeddingChunk(
                        transcript=transcript,
                        meeting=transcript.meeting,
                        organisation=transcript.organisation,
                        created_by=transcript.created_by,
                        chunk_text=chunk['chunk_text'],
                        embedding=chunk['embedding'],
                        chunk_index=chunk['chunk_index'],
                        start_seconds=chunk.get('start_seconds'),
                        end_seconds=chunk.get('end_seconds'),
                    )
                    for chunk in chunks_data
                ]
                EmbeddingChunk.objects.bulk_create(chunks_to_create)

        except Exception as e:
            logger.error(
                'Failed to store embeddings for transcript %s: %s',
                transcript_id, e
            )
            return error_response(
                code='storage_error',
                message='Failed to store embeddings',
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        logger.info(
            'Stored %s embedding chunks for transcript %s',
            len(chunks_to_create),
            transcript_id
        )

        return success_response(
            message='Embeddings stored',
            data={'chunks_stored': len(chunks_to_create)},
            status_code=status.HTTP_201_CREATED
        )