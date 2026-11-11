from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator


class Client(models.Model):
    """Карта гостя. Бонусы — целые. Минуты абонемента — целые."""

    STATUS_CLIENT = "client"
    STATUS_ADMIN = "admin"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_CLIENT, "Клиент"),
        (STATUS_ADMIN, "Администратор (бот)"),
        (STATUS_BLOCKED, "Заблокирован"),
    ]

    # ---- уровни ----
    LEVEL_STANDARD = "standard"
    LEVEL_AMATEUR = "amateur"      # Любитель  — от 5 000 ₽
    LEVEL_MASTER = "master"        # Мастер    — от 10 000 ₽
    LEVEL_PREMIUM = "premium"      # Премиум   — от 20 000 ₽
    LEVEL_CHOICES = [
        (LEVEL_STANDARD, "Стандарт"),
        (LEVEL_AMATEUR, "Любитель"),
        (LEVEL_MASTER, "Мастер"),
        (LEVEL_PREMIUM, "Премиум"),
    ]
    # порог суммы оплат деньгами -> (уровень, процент кэшбека)
    LEVEL_THRESHOLDS = [
        (20000, LEVEL_PREMIUM, 10),
        (10000, LEVEL_MASTER, 8),
        (5000, LEVEL_AMATEUR, 7),
        (0, LEVEL_STANDARD, 5),
    ]
    CASHBACK_BY_LEVEL = {
        LEVEL_STANDARD: 5,
        LEVEL_AMATEUR: 7,
        LEVEL_MASTER: 8,
        LEVEL_PREMIUM: 10,
    }

    telegram_id = models.BigIntegerField(unique=True, verbose_name="Telegram ID")
    name = models.CharField(max_length=255, verbose_name="Имя")
    phone = models.CharField(max_length=20, unique=True, db_index=True, verbose_name="Телефон")
    balance = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Бонусы",
    )
    minutes = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Минуты абонемента",
    )
    total_paid = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Сумма оплат деньгами, ₽",
        help_text="Сумма денег по всем проведённым визитам. Используется для уровня.",
    )
    level = models.CharField(
        max_length=10,
        choices=LEVEL_CHOICES,
        default=LEVEL_STANDARD,
        db_index=True,
        verbose_name="Уровень",
        help_text="Уровень не снижается при обычных визитах; пересчитывается при отмене.",
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

    @property
    def cashback_percent(self) -> int:
        return self.CASHBACK_BY_LEVEL[self.level]

    @property
    def level_display(self) -> str:
        return dict(self.LEVEL_CHOICES)[self.level]

    def next_level_info(self):
        """Возвращает (следующий_уровень_или_None, сколько_₽_осталось)."""
        order = [self.LEVEL_STANDARD, self.LEVEL_AMATEUR, self.LEVEL_MASTER, self.LEVEL_PREMIUM]
        try:
            idx = order.index(self.level)
        except ValueError:
            return None, 0
        if idx >= len(order) - 1:
            return None, 0
        next_level = order[idx + 1]
        threshold = dict((lvl, thr) for thr, lvl, _ in self.LEVEL_THRESHOLDS)[next_level]
        left = max(0, threshold - self.total_paid)
        return next_level, left


class Visit(models.Model):
    """Визит. Одна запись = один чек. Может быть отменён."""

    STATUS_DONE = "done"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DONE, "Проведён"),
        (STATUS_CANCELLED, "Отменён"),
    ]

    client = models.ForeignKey(
        Client, on_delete=models.PROTECT,
        related_name="visits", verbose_name="Клиент",
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="Администратор (веб)",
    )
    performed_by_tg = models.BigIntegerField(
        null=True, blank=True,
        verbose_name="Кто провёл в боте (Telegram ID)",
    )

    price = models.PositiveIntegerField(verbose_name="Цена визита, ₽")
    minutes_used = models.PositiveIntegerField(default=0, verbose_name="Списано минут")
    bonuses_used = models.PositiveIntegerField(default=0, verbose_name="Списано бонусов")
    money_paid = models.PositiveIntegerField(verbose_name="Оплачено деньгами, ₽")
    cashback_percent = models.PositiveIntegerField(verbose_name="Процент кэшбека")
    cashback = models.PositiveIntegerField(verbose_name="Начислено бонусов")

    balance_before = models.IntegerField(verbose_name="Бонусы до")
    balance_after = models.IntegerField(verbose_name="Бонусы после")
    minutes_before = models.IntegerField(verbose_name="Минуты до")
    minutes_after = models.IntegerField(verbose_name="Минуты после")
    total_paid_before = models.IntegerField(verbose_name="Сумма оплат до")
    total_paid_after = models.IntegerField(verbose_name="Сумма оплат после")

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES,
        default=STATUS_DONE, db_index=True, verbose_name="Статус",
    )
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата отмены")

    class Meta:
        verbose_name = "Визит"
        verbose_name_plural = "Визиты"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["client", "-created_at"])]

    def __str__(self):
        return f"Визит {self.price}₽ — {self.client.phone}"


class MinuteGrant(models.Model):
    """Назначение минут (покупка абонемента / подарок). Не меняет бонусы и уровень."""

    client = models.ForeignKey(
        Client, on_delete=models.PROTECT,
        related_name="minute_grants", verbose_name="Клиент",
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="Администратор (веб)",
    )
    performed_by_tg = models.BigIntegerField(
        null=True, blank=True,
        verbose_name="Кто назначил в боте (Telegram ID)",
    )
    minutes = models.PositiveIntegerField(verbose_name="Назначено минут")
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    minutes_before = models.IntegerField(verbose_name="Минуты до")
    minutes_after = models.IntegerField(verbose_name="Минуты после")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")

    class Meta:
        verbose_name = "Назначение минут"
        verbose_name_plural = "Назначения минут"
        ordering = ["-created_at"]