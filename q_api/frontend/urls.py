from django.urls import path

from frontend.views import *

urlpatterns = [
    path("login", Login.as_view()),
    path("logout", Logout.as_view()),
    path("", DashboardView.as_view()),
]