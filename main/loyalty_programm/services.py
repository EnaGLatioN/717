import re
from django.db import transaction
from django.core.exceptions import ValidationError
from .models import Client, Operation


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
    """
    Возвращает (client, created, conflict), где conflict — чужой Client,
    если этот номер уже занят другим telegram_id.
    """
    phone = normalize_phone(phone)

    # 1) по telegram_id
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

    # 2) новый telegram_id — номер занят?
    by_phone = Client.objects.filter(phone=phone).first()
    if by_phone:
        return None, False, by_phone

    client = Client.objects.create(telegram_id=telegram_id, name=name, phone=phone)
    return client, True, None


def is_tg_admin(telegram_id: int) -> bool:
    return Client.objects.filter(
        telegram_id=telegram_id, is_bot_admin=True,
    ).exists()


@transaction.atomic
def change_status(client: Client, new_status: str) -> Client:
    if new_status not in dict(Client.STATUS_CHOICES):
        raise ValidationError("Неизвестный статус")
    client = Client.objects.select_for_update().get(pk=client.pk)
    client.status = new_status
    client.save(update_fields=["status"])
    return client


@transaction.atomic
def accrue(client: Client, amount: int, admin=None, comment: str = "",
           performed_by_tg: int | None = None):
    if amount <= 0:
        raise ValidationError("Сумма начисления должна быть больше нуля")
    client = Client.objects.select_for_update().get(pk=client.pk)
    before = client.balance
    client.balance = before + amount
    client.save(update_fields=["balance"])
    return Operation.objects.create(
        client=client, type=Operation.TYPE_ACCRUAL, amount=amount,
        comment=comment, admin=admin, performed_by_tg=performed_by_tg,
        balance_before=before, balance_after=client.balance,
    )


@transaction.atomic
def write_off(client: Client, amount: int, admin=None, comment: str = "",
              performed_by_tg: int | None = None):
    if amount <= 0:
        raise ValidationError("Сумма списания должна быть больше нуля")
    client = Client.objects.select_for_update().get(pk=client.pk)
    if amount > client.balance:
        raise ValidationError("Списание больше текущего баланса")
    before = client.balance
    client.balance = before - amount
    client.save(update_fields=["balance"])
    return Operation.objects.create(
        client=client, type=Operation.TYPE_WRITE_OFF, amount=amount,
        comment=comment, admin=admin, performed_by_tg=performed_by_tg,
        balance_before=before, balance_after=client.balance,
    )