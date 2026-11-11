import re
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Client, Visit, MinuteGrant


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or digits[0] != "7":
        raise ValidationError("Некорректный номер телефона")
    return "+" + digits


def format_phone(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11:
        return f"+{d[0]} ({d[1:4]}) {d[4:7]}-{d[7:9]}-{d[9:11]}"
    return phone


def get_or_create_client(telegram_id: int, name: str, phone: str):
    phone = normalize_phone(phone)

    client = Client.objects.filter(telegram_id=telegram_id).first()
    if client:
        if client.phone == phone:
            return client, False, None
        other = Client.objects.filter(phone=phone).exclude(pk=client.pk).first()
        if other:
            return client, False, other
        client.phone = phone
        client.save(update_fields=["phone"])
        return client, False, None

    by_phone = Client.objects.filter(phone=phone).first()
    if by_phone:
        return None, False, by_phone

    client = Client.objects.create(telegram_id=telegram_id, name=name, phone=phone)
    return client, True, None


def is_tg_admin(telegram_id: int) -> bool:
    return Client.objects.filter(telegram_id=telegram_id, is_bot_admin=True).exists()


@transaction.atomic
def change_status(client: Client, new_status: str) -> Client:
    if new_status not in dict(Client.STATUS_CHOICES):
        raise ValidationError("Неизвестный статус")
    client = Client.objects.select_for_update().get(pk=client.pk)
    client.status = new_status
    client.save(update_fields=["status"])
    return client


# ---------- уровни ----------

def calc_level(total_paid: int) -> str:
    """Уровень по сумме оплат деньгами."""
    for threshold, level, _ in Client.LEVEL_THRESHOLDS:
        if total_paid >= threshold:
            return level
    return Client.LEVEL_STANDARD


def calc_cashback(money: int, percent: int) -> int:
    """Округление 0,5 вверх."""
    # money и percent — целые. round half up: (money*percent + 50) // 100
    return (money * percent + 50) // 100


# ---------- проверки визита ----------

def validate_visit_inputs(price: int, bonuses: int, minutes: int, client: Client):
    if price < 0:
        raise ValidationError("Цена визита не может быть отрицательной")
    if bonuses < 0:
        raise ValidationError("Бонусы к списанию не могут быть отрицательными")
    if minutes < 0:
        raise ValidationError("Минуты не могут быть отрицательными")
    max_bonus_by_check = price // 2          # половина чека, округление вниз
    if bonuses > max_bonus_by_check:
        raise ValidationError(
            f"Бонусами можно закрыть не больше половины чека: {max_bonus_by_check}"
        )
    if bonuses > client.balance:
        raise ValidationError("Бонусов к списанию больше текущего баланса")
    if minutes > client.minutes:
        raise ValidationError("Минут к списанию больше остатка абонемента")


def preview_visit(client: Client, price: int, bonuses: int, minutes: int) -> dict:
    """Рассчитывает визит без сохранения. Для подтверждения в UI."""
    validate_visit_inputs(price, bonuses, minutes, client)
    money = price - bonuses
    percent = Client.CASHBACK_BY_LEVEL[client.level]
    cashback = calc_cashback(money, percent)
    new_total_paid = client.total_paid + money
    new_level = calc_level(new_total_paid)
    return {
        "price": price,
        "bonuses_used": bonuses,
        "minutes_used": minutes,
        "money_paid": money,
        "cashback_percent": percent,
        "cashback": cashback,
        "balance_before": client.balance,
        "balance_after": client.balance - bonuses + cashback,
        "minutes_before": client.minutes,
        "minutes_after": client.minutes - minutes,
        "total_paid_before": client.total_paid,
        "total_paid_after": new_total_paid,
        "level_before": client.level,
        "level_after": new_level,   # для отображения «уровень вырастет»
    }


# ---------- визит ----------

@transaction.atomic
def conduct_visit(client: Client, price: int, bonuses: int, minutes: int,
                  admin=None, comment: str = "", performed_by_tg: int | None = None) -> Visit:
    """Проводит визит. Процент кэшбека — по уровню ДО визита."""
    client = Client.objects.select_for_update().get(pk=client.pk)
    validate_visit_inputs(price, bonuses, minutes, client)

    money = price - bonuses
    percent = Client.CASHBACK_BY_LEVEL[client.level]
    cashback = calc_cashback(money, percent)

    balance_before = client.balance
    minutes_before = client.minutes
    total_paid_before = client.total_paid

    client.balance = balance_before - bonuses + cashback
    client.minutes = minutes_before - minutes
    client.total_paid = total_paid_before + money
    client.level = calc_level(client.total_paid)  # после визита
    client.save(update_fields=["balance", "minutes", "total_paid", "level"])

    return Visit.objects.create(
        client=client, admin=admin, performed_by_tg=performed_by_tg,
        price=price, minutes_used=minutes, bonuses_used=bonuses,
        money_paid=money, cashback_percent=percent, cashback=cashback,
        balance_before=balance_before, balance_after=client.balance,
        minutes_before=minutes_before, minutes_after=client.minutes,
        total_paid_before=total_paid_before, total_paid_after=client.total_paid,
        comment=comment, status=Visit.STATUS_DONE,
    )


@transaction.atomic
def cancel_visit(visit: Visit) -> Visit:
    """Отменяет визит: возвращает минуты, бонусы, вычитает деньги из суммы уровня."""
    if visit.status == Visit.STATUS_CANCELLED:
        raise ValidationError("Визит уже отменён")

    client = Client.objects.select_for_update().get(pk=visit.client_id)

    # откатываем изменения
    client.balance = client.balance + visit.bonuses_used - visit.cashback
    client.minutes = client.minutes + visit.minutes_used
    client.total_paid = client.total_paid - visit.money_paid
    if client.balance < 0:
        raise ValidationError(
            "Нельзя отменить визит: бонусов на балансе не хватает "
            "(часть начисленного кэшбека уже потрачена)"
        )
    client.level = calc_level(client.total_paid)
    client.save(update_fields=["balance", "minutes", "total_paid", "level"])

    visit.status = Visit.STATUS_CANCELLED
    visit.cancelled_at = timezone.now()
    visit.save(update_fields=["status", "cancelled_at"])
    return visit


# ---------- минуты ----------

@transaction.atomic
def grant_minutes(client: Client, minutes: int, admin=None, comment: str = "",
                  performed_by_tg: int | None = None) -> MinuteGrant:
    if minutes <= 0:
        raise ValidationError("Количество минут должно быть больше нуля")
    client = Client.objects.select_for_update().get(pk=client.pk)
    before = client.minutes
    client.minutes = before + minutes
    client.save(update_fields=["minutes"])
    return MinuteGrant.objects.create(
        client=client, admin=admin, performed_by_tg=performed_by_tg,
        minutes=minutes, comment=comment,
        minutes_before=before, minutes_after=client.minutes,
    )