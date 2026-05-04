class NotificationType:
    MEETING_STARTED      = 'meeting_started'
    TRANSCRIPTION_DONE   = 'transcription_done'
    SUMMARY_DONE         = 'summary_done'
    MEMBER_JOINED        = 'member_joined'
    INVITE_ACCEPTED      = 'invite_accepted'
    BOT_FAILED           = 'bot_failed'


NOTIFICATION_TITLES = {
    NotificationType.MEETING_STARTED:    'Meeting started',
    NotificationType.TRANSCRIPTION_DONE: 'Transcript ready',
    NotificationType.SUMMARY_DONE:       'Summary ready',
    NotificationType.MEMBER_JOINED:      'New member joined',
    NotificationType.INVITE_ACCEPTED:    'Invite accepted',
    NotificationType.BOT_FAILED:         'Bot could not join',
}
