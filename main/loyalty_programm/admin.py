from django.contrib import admin
from .models import Client, Operation


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "name", "phone", "balance", "is_bot_admin", "status",
        "telegram_id", "created_at",
    )
    list_filter = ("is_bot_admin", "status", "created_at")
    search_fields = ("name", "phone", "telegram_id")
    list_editable = ("is_bot_admin", "status")
    readonly_fields = ("telegram_id", "created_at", "balance")
    fieldsets = (
        ("Клиент", {
            "fields": ("name", "phone", "telegram_id", "created_at"),
        }),
        ("Бонусы", {
            "fields": ("balance",),
            "description": "Баланс меняется только через операции — "
                           "используйте кнопки «Начислить/Списать» в боте или веб-админке.",
        }),
        ("Доступ", {
            "fields": ("is_bot_admin", "status"),
        }),
    )


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "client", "type", "amount",
        "balance_before", "balance_after",
        "admin", "performed_by_tg", "comment",
    )
    list_filter = ("type", "created_at")
    search_fields = ("client__phone", "client__name", "comment")
    readonly_fields = (
        "client", "type", "amount", "comment", "admin", "performed_by_tg",
        "created_at", "balance_before", "balance_after",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
    