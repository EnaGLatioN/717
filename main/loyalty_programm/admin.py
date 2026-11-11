from django.contrib import admin
from .models import Client, Visit, MinuteGrant


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "name", "phone", "balance", "minutes", "level", "total_paid",
        "is_bot_admin", "status", "telegram_id", "created_at",
    )
    list_filter = ("level", "is_bot_admin", "status", "created_at")
    search_fields = ("name", "phone", "telegram_id")
    list_editable = ("is_bot_admin", "status")
    readonly_fields = (
        "telegram_id", "created_at", "balance", "minutes",
        "total_paid", "level",
    )
    fieldsets = (
        ("Клиент", {"fields": ("name", "phone", "telegram_id", "created_at")}),
        ("Бонусы / минуты / уровень", {
            "fields": ("balance", "minutes", "total_paid", "level"),
            "description": "Бонусы, минуты и уровень меняются через визиты и назначение минут.",
        }),
        ("Доступ", {"fields": ("is_bot_admin", "status")}),
    )


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "client", "status", "price", "bonuses_used",
        "minutes_used", "money_paid", "cashback_percent", "cashback",
        "admin", "performed_by_tg",
    )
    list_filter = ("status", "created_at")
    search_fields = ("client__phone", "client__name", "comment")
    readonly_fields = (
        "client", "admin", "performed_by_tg", "price", "minutes_used",
        "bonuses_used", "money_paid", "cashback_percent", "cashback",
        "balance_before", "balance_after", "minutes_before", "minutes_after",
        "total_paid_before", "total_paid_after", "status", "created_at",
        "cancelled_at", "comment",
    )

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(MinuteGrant)
class MinuteGrantAdmin(admin.ModelAdmin):
    list_display = ("created_at", "client", "minutes", "minutes_before",
                    "minutes_after", "admin", "performed_by_tg", "comment")
    list_filter = ("created_at",)
    search_fields = ("client__phone", "client__name", "comment")
    readonly_fields = (
        "client", "admin", "performed_by_tg", "minutes", "comment",
        "minutes_before", "minutes_after", "created_at",
    )

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False