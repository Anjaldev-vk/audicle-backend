from django.urls import path
from .views import ActionItemDetailView, ActionItemCrossView

urlpatterns = [
    path('', ActionItemCrossView.as_view()),
    path('<uuid:item_id>/', ActionItemDetailView.as_view()),
]
