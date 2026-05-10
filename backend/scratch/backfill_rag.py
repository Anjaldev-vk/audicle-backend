from rag.models import ChatMessage
from analytics.tasks import track_rag_query
import logging

logging.getLogger('analytics').setLevel(logging.WARNING)

# Only track user messages (AI queries)
messages = ChatMessage.objects.filter(role='user')
print(f"Backfilling {messages.count()} RAG queries...")

for msg in messages:
    workspace_id = str(msg.session.organisation.id) if msg.session.organisation else str(msg.session.user.id)
    track_rag_query.delay(
        user_id=str(msg.session.user.id),
        workspace_id=workspace_id,
        session_id=str(msg.session.id)
    )

print("RAG backfill queued!")
