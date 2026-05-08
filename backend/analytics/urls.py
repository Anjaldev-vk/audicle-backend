from django.urls import path
from .views import (
    AnalyticsOverviewView,
    AnalyticsMeetingsChartView,
    AnalyticsActivityView,
    AnalyticsTeamOverviewView,
    AnalyticsTeamMembersView,
)

urlpatterns = [
    path('overview/',      AnalyticsOverviewView.as_view()),
    path('meetings/',      AnalyticsMeetingsChartView.as_view()),
    path('activity/',      AnalyticsActivityView.as_view()),
    path('team/overview/', AnalyticsTeamOverviewView.as_view()),
    path('team/members/',  AnalyticsTeamMembersView.as_view()),
]
