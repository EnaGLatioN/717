from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView

from loyalty_programm import views as loyalty_views


urlpatterns = [
    path("", RedirectView.as_view(url="/loyalty/", permanent=False)),
    path("admin/", admin.site.urls),

    path("loyalty/", loyalty_views.search, name="search"),
    path("loyalty/login/", auth_views.LoginView.as_view(
        template_name="loyalty_programm/login.html"), name="login"),
    path("loyalty/logout/", auth_views.LogoutView.as_view(
        next_page="login", http_method_names=["get", "post", "options"]), name="logout"),

    path("loyalty/client/<int:pk>/", loyalty_views.client_detail, name="client"),
    path("loyalty/client/<int:pk>/visit/", loyalty_views.visit_create, name="visit_create"),
    path("loyalty/client/<int:pk>/visit/<int:visit_pk>/cancel/",
         loyalty_views.visit_cancel, name="visit_cancel"),
    path("loyalty/client/<int:pk>/grant-minutes/",
         loyalty_views.grant_minutes_view, name="grant_minutes"),
    path("loyalty/client/<int:pk>/toggle-admin/",
         loyalty_views.client_toggle_admin, name="toggle_admin"),
]