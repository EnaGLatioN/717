from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView

from loyalty_programm import views as loyalty_views


urlpatterns = [
    # Редирект с корня на админку лояльности
    path("", RedirectView.as_view(url="/loyalty/", permanent=False)),

    # Django-админка (суперюзер) — там галочка «Админ бота»
    path("admin/", admin.site.urls),

    # ===== Программа лояльности =====
    path("loyalty/", loyalty_views.search, name="search"),
    path(
        "loyalty/login/",
        auth_views.LoginView.as_view(
            template_name="loyalty_programm/login.html",
        ),
        name="login",
    ),
    path(
        "loyalty/logout/",
        auth_views.LogoutView.as_view(
            next_page="login",
            http_method_names=["get", "post", "options"],
        ),
        name="logout",
    ),
    path("loyalty/client/<int:pk>/", loyalty_views.client_detail, name="client"),
    path(
        "loyalty/client/<int:pk>/toggle-admin/",
        loyalty_views.client_toggle_admin,
        name="toggle_admin",
    ),
    path(
        "loyalty/client/<int:pk>/<str:op_type>/",
        loyalty_views.client_operation,
        name="operation",
    ),
]