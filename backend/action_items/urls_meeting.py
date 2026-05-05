from django.urls import path
from .views import MeetingActionItemListCreateView

urlpatterns = [
    path('', MeetingActionItemListCreateView.as_view()),
]
