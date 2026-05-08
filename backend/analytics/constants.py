class EventType:
    MEETING_CREATED        = 'meeting_created'
    MEETING_COMPLETED      = 'meeting_completed'
    BOT_JOINED             = 'bot_joined'
    TRANSCRIPTION_DONE     = 'transcription_done'
    SUMMARY_DONE           = 'summary_done'
    ACTION_ITEM_CREATED    = 'action_item_created'
    ACTION_ITEM_COMPLETED  = 'action_item_completed'
    RAG_QUERY              = 'rag_query'
    MEMBER_JOINED          = 'member_joined'

    ALL = [
        MEETING_CREATED,
        MEETING_COMPLETED,
        BOT_JOINED,
        TRANSCRIPTION_DONE,
        SUMMARY_DONE,
        ACTION_ITEM_CREATED,
        ACTION_ITEM_COMPLETED,
        RAG_QUERY,
        MEMBER_JOINED,
    ]


PERIOD_DAYS = {
    '7d':  7,
    '30d': 30,
    '90d': 90,
}