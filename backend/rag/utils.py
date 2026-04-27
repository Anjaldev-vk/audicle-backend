import logging
from pgvector.django import CosineDistance
from .models import EmbeddingChunk

logger = logging.getLogger('rag')


def get_queryset_for_user(user):
    """
    Scope EmbeddingChunk queryset to the user's tenant.
    Org users → organisation scope.
    Individual users → created_by scope.
    """
    if user.organisation:
        return EmbeddingChunk.objects.filter(
            organisation=user.organisation
        ).select_related('meeting', 'transcript')
    return EmbeddingChunk.objects.filter(
        created_by=user
    ).select_related('meeting', 'transcript')


def search_similar_chunks(user, query_embedding, meeting_id=None, limit=5):
    """
    Perform cosine similarity search against pgvector.
    Returns top `limit` chunks ordered by similarity.
    """
    qs = get_queryset_for_user(user)

    if meeting_id:
        qs = qs.filter(meeting_id=meeting_id)

    results = (
        qs
        .annotate(distance=CosineDistance('embedding', query_embedding))
        .order_by('distance')[:limit]
    )

    logger.info(
        'RAG search returned %s chunks for user %s',
        len(results),
        user.id
    )

    return results


def build_context_from_chunks(chunks):
    """
    Build a readable context string from retrieved chunks.
    Includes meeting title and timestamp for each chunk.
    """
    context_parts = []
    for chunk in chunks:
        meeting_title = chunk.meeting.title
        time_info = ''
        if chunk.start_seconds is not None:
            mins = int(chunk.start_seconds // 60)
            secs = int(chunk.start_seconds % 60)
            time_info = f' [{mins:02d}:{secs:02d}]'
        context_parts.append(
            f"[Meeting: {meeting_title}{time_info}]\n{chunk.chunk_text}"
        )
    return '\n\n---\n\n'.join(context_parts)


def build_search_prompt(query, context):
    return f"""You are an AI assistant with access to meeting transcripts.
Answer the user's question using ONLY the context provided below.
If the answer is not in the context, say "I could not find relevant information in the meeting transcripts."
Always cite which meeting the information came from.

CONTEXT:
{context}

QUESTION:
{query}

ANSWER:"""


def build_chat_prompt(query, context, history):
    """
    Build prompt for multi-turn chat with history.
    """
    history_text = ''
    for msg in history:
        role = 'User' if msg.role == 'user' else 'Assistant'
        history_text += f"{role}: {msg.content}\n"

    return f"""You are an AI assistant with access to meeting transcripts.
Answer using ONLY the context provided. Cite meeting names.
If the answer is not in the context, say so clearly.

MEETING CONTEXT:
{context}

CONVERSATION HISTORY:
{history_text}
User: {query}

ANSWER:"""