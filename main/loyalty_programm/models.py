from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator


class Client(models.Model):
    """Карта клиента. Баланс — целые бонусы."""

    STATUS_CLIENT = "client"
    STATUS_ADMIN = "admin"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_CLIENT, "Клиент"),
        (STATUS_ADMIN, "Администратор (бот)"),
        (STATUS_BLOCKED, "Заблокирован"),
    ]

    telegram_id = models.BigIntegerField(unique=True, verbose_name="Telegram ID")
    name = models.CharField(max_length=255, verbose_name="Имя")
    phone = models.CharField(max_length=20, unique=True, db_index=True, verbose_name="Телефон")
    balance = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Баланс",
    )
    is_bot_admin = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Админ бота",
        help_text="Поставьте галочку — пользователь получит доступ к /admin в Telegram.",
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_CLIENT,
        db_index=True,
        verbose_name="Статус",
        help_text="Блокировка блокирует доступ к боту. Админ-флаг — отдельно.",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата регистрации")

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        ordering = ["-created_at"]

    def __str__(self):
        marker = " [АДМИН]" if self.is_bot_admin else ""
        return f"{self.name} ({self.phone}) — {self.balance} бонусов{marker}"

    @property
    def is_blocked(self) -> bool:
        return self.status == self.STATUS_BLOCKED


class Operation(models.Model):
    TYPE_ACCRUAL = "accrual"
    TYPE_WRITE_OFF = "write_off"
    TYPE_CHOICES = [
        (TYPE_ACCRUAL, "Начисление"),
        (TYPE_WRITE_OFF, "Списание"),
    ]

    client = models.ForeignKey(
        Client, on_delete=models.PROTECT,
        related_name="operations", verbose_name="Клиент",
    )
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name="Тип")
    amount = models.PositiveIntegerField(verbose_name="Сумма")
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,                # ← стандартный User, не своя модель
        on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="Администратор (веб)",
    )
    performed_by_tg = models.BigIntegerField(
        null=True, blank=True,
        verbose_name="Кто сделал в боте (Telegram ID)",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")
    balance_before = models.IntegerField(verbose_name="Баланс до")
    balance_after = models.IntegerField(verbose_name="Баланс после")

    class Meta:
        verbose_name = "Операция"
        verbose_name_plural = "Операции"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["client", "-created_at"])]

    def __str__(self):
        sign = "+" if self.type == self.TYPE_ACCRUAL else "-"
        return f"{sign}{self.amount} — {self.client.phone}"

