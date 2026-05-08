from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from utils.response import success_response, error_response
from accounts.permissions import IsOrgAdmin
from .constants import PERIOD_DAYS
from .analytics_service import (
    get_workspace_id,
    build_overview,
    build_meetings_chart,
    build_activity_chart,
    build_team_overview,
    build_team_members,
)
import logging

logger = logging.getLogger(__name__)


def _get_days(request):
    """Parse ?period=7d|30d|90d from request."""
    period = request.query_params.get('period', '30d')
    return PERIOD_DAYS.get(period, 30)


class AnalyticsOverviewView(APIView):
    """
    GET /api/v1/analytics/overview/?period=30d
    Personal or org overview based on workspace context.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace_id = get_workspace_id(request)
        days         = _get_days(request)

        try:
            data = build_overview(workspace_id, days)
            return success_response(
                data=data,
                message='Overview fetched',
            )
        except Exception as e:
            logger.error('AnalyticsOverviewView error: %s', e)
            return error_response(
                code='analytics_error',
                message='Failed to fetch analytics',
                status_code=502,
            )


class AnalyticsMeetingsChartView(APIView):
    """
    GET /api/v1/analytics/meetings/?period=30d
    Meeting frequency chart data grouped by day.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace_id = get_workspace_id(request)
        days         = _get_days(request)

        try:
            data = build_meetings_chart(workspace_id, days)
            return success_response(
                data={'chart': data, 'period_days': days},
                message='Meetings chart fetched',
            )
        except Exception as e:
            logger.error('AnalyticsMeetingsChartView error: %s', e)
            return error_response(
                code='analytics_error',
                message='Failed to fetch meetings chart',
                status_code=502,
            )


class AnalyticsActivityView(APIView):
    """
    GET /api/v1/analytics/activity/?period=30d
    All event types over time for charts.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace_id = get_workspace_id(request)
        days         = _get_days(request)

        try:
            data = build_activity_chart(workspace_id, days)
            return success_response(
                data={'activity': data, 'period_days': days},
                message='Activity chart fetched',
            )
        except Exception as e:
            logger.error('AnalyticsActivityView error: %s', e)
            return error_response(
                code='analytics_error',
                message='Failed to fetch activity',
                status_code=502,
            )


class AnalyticsTeamOverviewView(APIView):
    """
    GET /api/v1/analytics/team/overview/?period=30d
    Org admin only — team-wide overview.
    """
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get(self, request):
        if not request.organisation:
            return error_response(
                code='org_required',
                message='Team analytics requires an '
                        'organisation workspace.',
                status_code=400,
            )

        workspace_id = str(request.organisation.id)
        days         = _get_days(request)

        try:
            data = build_team_overview(workspace_id, days)
            return success_response(
                data=data,
                message='Team overview fetched',
            )
        except Exception as e:
            logger.error('AnalyticsTeamOverviewView error: %s', e)
            return error_response(
                code='analytics_error',
                message='Failed to fetch team analytics',
                status_code=502,
            )


class AnalyticsTeamMembersView(APIView):
    """
    GET /api/v1/analytics/team/members/?period=30d
    Org admin only — per-member activity breakdown.
    """
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get(self, request):
        if not request.organisation:
            return error_response(
                code='org_required',
                message='Team analytics requires an '
                        'organisation workspace.',
                status_code=400,
            )

        workspace_id = str(request.organisation.id)
        days         = _get_days(request)

        try:
            data = build_team_members(workspace_id, days)
            return success_response(
                data={
                    'members':     data,
                    'period_days': days,
                },
                message='Team members analytics fetched',
            )
        except Exception as e:
            logger.error('AnalyticsTeamMembersView error: %s', e)
            return error_response(
                code='analytics_error',
                message='Failed to fetch team members analytics',
                status_code=502,
            )
