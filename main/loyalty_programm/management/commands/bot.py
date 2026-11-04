import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
)
from django.core.management.base import BaseCommand
from django.conf import settings

from loyalty_programm.models import Client, Operation
from loyalty_programm.services import (
    get_or_create_client, format_phone, is_tg_admin,
)

from . import admin_handlers


def main_keyboard(client: Client | None = None, is_admin: bool = False):
    """
    Собираем клавиатуру под состояние клиента.
    Кнопка «Поделиться контактом» появляется ТОЛЬКО если у клиента ещё нет телефона.
    """
    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    has_phone = bool(client and client.phone)
    if not has_phone:
        kb.add(KeyboardButton("📱 Поделиться контактом", request_contact=True))

    kb.add(KeyboardButton("💳 Мой баланс"), KeyboardButton("📜 История"))
    if is_admin:
        kb.add(KeyboardButton("🛠 Админ-панель"))
    return kb


class Command(BaseCommand):
    help = "Telegram-бот программы лояльности"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = telebot.TeleBot(settings.TELE_TOKEN, parse_mode="HTML")
        self.ctx = admin_handlers.AdminContext(self.bot)
        self.setup_handlers()

    def _kb_for(self, tg_id: int):
        client = Client.objects.filter(telegram_id=tg_id).first()
        return main_keyboard(client=client, is_admin=is_tg_admin(tg_id))

    def _edit_or_send(self, message, text, reply_markup=None):
        chat_id = message.chat.id
        prev_id = self.ctx.live_msg.get(chat_id)
        if prev_id:
            try:
                self.bot.edit_message_text(
                    chat_id=chat_id, message_id=prev_id,
                    text=text, reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
                return
            except Exception:
                pass
        sent = self.bot.send_message(
            chat_id, text, reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        self.ctx.live_msg[chat_id] = sent.message_id

    def setup_handlers(self):

        @self.bot.message_handler(commands=["start"])
        def on_start(message):
            tg_id = message.from_user.id
            name = message.from_user.first_name or "Клиент"
            client = Client.objects.filter(telegram_id=tg_id).first()

            if client and client.is_blocked and not is_tg_admin(tg_id):
                self._edit_or_send(message, "🚫 Ваш доступ заблокирован.")
                return

            if client:
                text = (
                    f"👋 <b>{name}</b>, с возвращением!\n\n"
                    f"Ваш номер: <b>{format_phone(client.phone)}</b>\n"
                    f"Баланс: <b>{client.balance} бонусов</b>\n"
                    f"Статус: <b>{client.get_status_display()}</b>"
                )
                if client.is_bot_admin:
                    text += "\n\n🛠 Вы администратор. /admin — панель управления."
                self._edit_or_send(message, text, reply_markup=self._kb_for(tg_id))
                return

            # Нет клиента — предлагаем регистрацию
            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.add(KeyboardButton("📱 Поделиться контактом", request_contact=True))
            self._edit_or_send(
                message,
                f"👋 <b>{name}</b>, добро пожаловать в программу лояльности!\n\n"
                f"Нажмите «Поделиться контактом», чтобы зарегистрироваться.",
                reply_markup=kb,
            )

        @self.bot.message_handler(commands=["admin"])
        def on_admin(message):
            if not is_tg_admin(message.from_user.id):
                self._edit_or_send(message, "⛔ Нет доступа.", reply_markup=self._kb_for(message.from_user.id))
                return
            self.ctx.show_admin_menu(message.chat.id)

        @self.bot.message_handler(func=lambda m: m.text == "🛠 Админ-панель")
        def on_admin_button(message):
            if not is_tg_admin(message.from_user.id):
                self._edit_or_send(message, "⛔ Нет доступа.", reply_markup=self._kb_for(message.from_user.id))
                return
            self.ctx.show_admin_menu(message.chat.id)

        # ============================================================
        #  Поделиться контактом
        # ============================================================
        @self.bot.message_handler(content_types=["contact"])
        def on_contact(message):
            contact = message.contact
            tg_id = message.from_user.id

            # уже есть клиент с телефоном? — просто игнорим
            existing = Client.objects.filter(telegram_id=tg_id).first()
            if existing and existing.phone:
                self._edit_or_send(
                    message,
                    f"ℹ️ У вас уже привязан номер <b>{format_phone(existing.phone)}</b>.\n"
                    f"Если нужно изменить — попросите администратора.",
                    reply_markup=self._kb_for(tg_id),
                )
                return

            if contact.user_id != message.from_user.id:
                self._edit_or_send(
                    message,
                    "Пожалуйста, поделитесь именно своим контактом.",
                    reply_markup=self._kb_for(tg_id),
                )
                return

            name = " ".join(filter(None, [
                message.from_user.first_name, message.from_user.last_name,
            ])) or "Клиент"

            try:
                client, created, conflict = get_or_create_client(
                    telegram_id=tg_id, name=name, phone=contact.phone_number,
                )
            except Exception as e:
                self._edit_or_send(
                    message, f"Не удалось сохранить номер: {e}",
                    reply_markup=self._kb_for(tg_id),
                )
                return

            if conflict is not None:
                # номер занят другим — просто отказ
                self._edit_or_send(
                    message,
                    f"❌ <b>Этот номер уже привязан к другому пользователю.</b>\n\n"
                    f"📞 {format_phone(conflict.phone)}\n\n"
                    f"Регистрация невозможна. Если это ошибка — обратитесь к администратору.",
                    reply_markup=self._kb_for(tg_id),
                )
                return

            self._edit_or_send(
                message,
                f"✅ Регистрация завершена!\n\n"
                f"Ваш номер: <b>{format_phone(client.phone)}</b>\n"
                f"Баланс: <b>{client.balance} бонусов</b>",
                reply_markup=self._kb_for(tg_id),
            )

        @self.bot.message_handler(func=lambda m: m.text == "💳 Мой баланс")
        def on_balance(message):
            client = Client.objects.filter(telegram_id=message.from_user.id).first()
            if not client:
                self._edit_or_send(message, "Сначала зарегистрируйтесь: /start",
                                   reply_markup=self._kb_for(message.from_user.id))
                return
            self._edit_or_send(
                message,
                f"💳 <b>Баланс: {client.balance} бонусов</b>\n\n"
                f"📞 Номер: <b>{format_phone(client.phone)}</b>\n"
                f"👤 Имя: {client.name}\n"
                f"🏷 Статус: {client.get_status_display()}\n\n"
                f"Покажите этот экран сотруднику.",
                reply_markup=self._kb_for(message.from_user.id),
            )

        @self.bot.message_handler(func=lambda m: m.text == "📜 История")
        def on_history(message):
            client = Client.objects.filter(telegram_id=message.from_user.id).first()
            if not client:
                self._edit_or_send(message, "Сначала зарегистрируйтесь: /start",
                                   reply_markup=self._kb_for(message.from_user.id))
                return
            ops = client.operations.order_by("-created_at")[:10]
            if not ops:
                self._edit_or_send(message, "Операций пока нет.",
                                   reply_markup=self._kb_for(message.from_user.id))
                return
            lines = ["📜 <b>Последние операции:</b>\n"]
            for op in ops:
                sign = "+" if op.type == Operation.TYPE_ACCRUAL else "−"
                dt = op.created_at.strftime("%d.%m.%Y %H:%M")
                cmt = f" — {op.comment}" if op.comment else ""
                lines.append(f"{dt}  <b>{sign}{op.amount}</b>{cmt}")
            self._edit_or_send(
                message, "\n".join(lines),
                reply_markup=self._kb_for(message.from_user.id),
            )

        admin_handlers.register(self.bot, self.ctx, self._edit_or_send, None)

        @self.bot.message_handler(func=lambda m: True)
        def on_fallback(message):
            if self.ctx.handle_text_input(message):
                return
            self._edit_or_send(
                message,
                "Используйте кнопки ниже или /start.",
                reply_markup=self._kb_for(message.from_user.id),
            )

    def handle(self, *args, **options):
        self.stdout.write("Бот запущен...")
        while True:
            try:
                self.bot.polling(non_stop=True, timeout=30)
            except Exception as e:
                self.stderr.write(f"Ошибка polling: {e}")
                continue