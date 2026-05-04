import logging

from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
)
from django.db.models import F, Value, CharField
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from meetings.models import Meeting
from transcripts.models import MeetingSummary, Transcript
from utils.response import error_response, success_response

logger = logging.getLogger("search")

VALID_TYPES = {"meetings", "transcripts", "summaries"}


class MeetingSearchView(APIView):
    """
    GET /api/v1/search/?q=<query>&type=meetings|transcripts|summaries

    Full-text search across meetings, transcripts, and summaries.
    Scoped to the user's current workspace (personal or org).
    Results are ranked by relevance.

    Query params:
        q    — search term (required, min 2 chars)
        type — filter by resource type (optional, defaults to all)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        result_type = request.query_params.get("type", "all").strip().lower()

        # ── Validate ──────────────────────────────────────────────────────
        if not query:
            return error_response(
                message="Search query 'q' is required.",
                code="missing_query",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if len(query) < 2:
            return error_response(
                message="Search query must be at least 2 characters.",
                code="query_too_short",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if result_type != "all" and result_type not in VALID_TYPES:
            return error_response(
                message=f"Invalid type. Choose from: all, {', '.join(sorted(VALID_TYPES))}.",
                code="invalid_type",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # ── Build results ─────────────────────────────────────────────────
        organisation = getattr(request, "organisation", None)
        results = []

        if result_type in ("all", "meetings"):
            results += self._search_meetings(query, request.user, organisation)

        if result_type in ("all", "transcripts"):
            results += self._search_transcripts(query, request.user, organisation)

        if result_type in ("all", "summaries"):
            results += self._search_summaries(query, request.user, organisation)

        # Sort all combined results by rank descending
        results.sort(key=lambda x: x["rank"], reverse=True)

        # Strip internal rank field before returning
        for r in results:
            r.pop("rank")

        logger.info(
            "Search '%s' type=%s user=%s returned %d results",
            query,
            result_type,
            request.user.email,
            len(results),
        )

        return success_response(
            message=f"Found {len(results)} result(s) for '{query}'.",
            data={
                "query": query,
                "type": result_type,
                "total": len(results),
                "results": results,
            },
            status_code=status.HTTP_200_OK,
        )

    # ── Private search methods ────────────────────────────────────────────

    def _base_meeting_qs(self, user, organisation):
        """Scope meetings to current workspace."""
        if organisation:
            return Meeting.objects.filter(
                organisation=organisation,
                is_archived=False,
            )
        return Meeting.objects.filter(
            created_by=user,
            organisation__isnull=True,
            is_archived=False,
        )

    def _search_meetings(self, query, user, organisation):
        search_query = SearchQuery(query)
        vector = SearchVector("title", weight="A") + SearchVector(
            "description", weight="B"
        )

        meetings = (
            self._base_meeting_qs(user, organisation)
            .annotate(
                search=vector,
                rank=SearchRank(vector, search_query),
            )
            .filter(search=search_query)
            .filter(rank__gt=0.0)
            .order_by("-rank")
            .values(
                "id",
                "title",
                "description",
                "status",
                "platform",
                "scheduled_at",
                "created_at",
                "rank",
            )[:20]
        )

        return [
            {
                "type": "meeting",
                "id": str(m["id"]),
                "title": m["title"],
                "description": m["description"] or "",
                "status": m["status"],
                "platform": m["platform"],
                "scheduled_at": (
                    m["scheduled_at"].isoformat() if m["scheduled_at"] else None
                ),
                "created_at": m["created_at"].isoformat(),
                "rank": m["rank"],
            }
            for m in meetings
        ]

    def _search_transcripts(self, query, user, organisation):
        search_query = SearchQuery(query)
        vector = SearchVector("raw_text", weight="B")

        if organisation:
            qs = Transcript.objects.filter(
                organisation=organisation,
                status=Transcript.Status.COMPLETED,
            )
        else:
            qs = Transcript.objects.filter(
                created_by=user,
                organisation__isnull=True,
                status=Transcript.Status.COMPLETED,
            )

        transcripts = (
            qs.select_related("meeting")
            .annotate(
                search=vector,
                rank=SearchRank(vector, search_query),
            )
            .filter(search=search_query)
            .filter(rank__gt=0.0)
            .order_by("-rank")
            .values(
                "id",
                "meeting__id",
                "meeting__title",
                "language",
                "word_count",
                "created_at",
                "rank",
            )[:20]
        )

        return [
            {
                "type": "transcript",
                "id": str(t["id"]),
                "meeting_id": str(t["meeting__id"]),
                "meeting_title": t["meeting__title"],
                "language": t["language"],
                "word_count": t["word_count"],
                "created_at": t["created_at"].isoformat(),
                "rank": t["rank"],
            }
            for t in transcripts
        ]

    def _search_summaries(self, query, user, organisation):
        search_query = SearchQuery(query)
        vector = SearchVector("summary", weight="A")

        if organisation:
            qs = MeetingSummary.objects.filter(
                organisation=organisation,
                status=MeetingSummary.Status.COMPLETED,
            )
        else:
            qs = MeetingSummary.objects.filter(
                created_by=user,
                organisation__isnull=True,
                status=MeetingSummary.Status.COMPLETED,
            )

        summaries = (
            qs.select_related("meeting")
            .annotate(
                search=vector,
                rank=SearchRank(vector, search_query),
            )
            .filter(search=search_query)
            .filter(rank__gt=0.0)
            .order_by("-rank")
            .values(
                "id",
                "meeting__id",
                "meeting__title",
                "created_at",
                "rank",
            )[:20]
        )

        return [
            {
                "type": "summary",
                "id": str(s["id"]),
                "meeting_id": str(s["meeting__id"]),
                "meeting_title": s["meeting__title"],
                "created_at": s["created_at"].isoformat(),
                "rank": s["rank"],
            }
            for s in summaries
        ]
