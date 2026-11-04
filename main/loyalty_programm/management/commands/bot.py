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


def main_keyboard(is_admin: bool = False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("👤 Поделиться контактом", request_contact=True))
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

    def setup_handlers(self):

        @self.bot.message_handler(commands=["start"])
        def on_start(message):
            tg_id = message.from_user.id
            name = message.from_user.first_name or "Клиент"
            client = Client.objects.filter(telegram_id=tg_id).first()

            if client and client.is_blocked and not is_tg_admin(tg_id):
                self.bot.send_message(tg_id, "🚫 Ваш доступ заблокирован.")
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
                self.bot.send_message(
                    message.chat.id, text,
                    reply_markup=main_keyboard(is_tg_admin(tg_id)),
                )
                return

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.add(KeyboardButton("📱 Поделиться контактом", request_contact=True))
            self.bot.send_message(
                message.chat.id,
                f"👋 <b>{name}</b>, добро пожаловать в программу лояльности!\n\n"
                f"Нажмите «Поделиться контактом», чтобы зарегистрироваться.",
                reply_markup=kb,
            )

        @self.bot.message_handler(commands=["admin"])
        def on_admin(message):
            if not is_tg_admin(message.from_user.id):
                self.bot.send_message(message.chat.id, "⛔ Нет доступа.")
                return
            self.ctx.show_admin_menu(message.chat.id)

        @self.bot.message_handler(func=lambda m: m.text == "🛠 Админ-панель")
        def on_admin_button(message):
            if not is_tg_admin(message.from_user.id):
                self.bot.send_message(message.chat.id, "⛔ Нет доступа.")
                return
            self.ctx.show_admin_menu(message.chat.id)

        @self.bot.message_handler(content_types=["contact"])
        def on_contact(message):
            contact = message.contact
            if contact.user_id != message.from_user.id:
                self.bot.reply_to(message, "Пожалуйста, поделитесь именно своим контактом.")
                return
            name = " ".join(filter(None, [
                message.from_user.first_name, message.from_user.last_name,
            ])) or "Клиент"
            try:
                client, _ = get_or_create_client(
                    telegram_id=message.from_user.id,
                    name=name,
                    phone=contact.phone_number,
                )
            except Exception as e:
                self.bot.reply_to(message, f"Не удалось сохранить номер: {e}")
                return
            self.bot.send_message(
                message.chat.id,
                f"✅ Регистрация завершена!\n\n"
                f"Ваш номер: <b>{format_phone(client.phone)}</b>\n"
                f"Баланс: <b>{client.balance} бонусов</b>",
                reply_markup=main_keyboard(is_tg_admin(message.from_user.id)),
            )

        @self.bot.message_handler(func=lambda m: m.text == "💳 Мой баланс")
        def on_balance(message):
            client = Client.objects.filter(telegram_id=message.from_user.id).first()
            if not client:
                self.bot.reply_to(message, "Сначала зарегистрируйтесь: /start")
                return
            self.bot.send_message(
                message.chat.id,
                f"💳 <b>Баланс: {client.balance} бонусов</b>\n\n"
                f"📞 Номер: <b>{format_phone(client.phone)}</b>\n"
                f"👤 Имя: {client.name}\n"
                f"🏷 Статус: {client.get_status_display()}\n\n"
                f"Покажите этот экран сотруднику.",
                reply_markup=main_keyboard(is_tg_admin(message.from_user.id)),
            )

        @self.bot.message_handler(func=lambda m: m.text == "📜 История")
        def on_history(message):
            client = Client.objects.filter(telegram_id=message.from_user.id).first()
            if not client:
                self.bot.reply_to(message, "Сначала зарегистрируйтесь: /start")
                return
            ops = client.operations.order_by("-created_at")[:10]
            if not ops:
                self.bot.reply_to(message, "Операций пока нет.",
                                  reply_markup=main_keyboard(is_tg_admin(message.from_user.id)))
                return
            lines = ["📜 <b>Последние операции:</b>\n"]
            for op in ops:
                sign = "+" if op.type == Operation.TYPE_ACCRUAL else "−"
                dt = op.created_at.strftime("%d.%m.%Y %H:%M")
                cmt = f" — {op.comment}" if op.comment else ""
                lines.append(f"{dt}  <b>{sign}{op.amount}</b>{cmt}")
            self.bot.send_message(
                message.chat.id, "\n".join(lines),
                reply_markup=main_keyboard(is_tg_admin(message.from_user.id)),
            )

        # === Регистрируем все админ-callback'и и текстовые вводы ===
        admin_handlers.register(self.bot, self.ctx)

        @self.bot.message_handler(func=lambda m: True)
        def on_fallback(message):
            # Если пользователь в процессе ввода в админке — отдаём ctx
            if self.ctx.handle_text_input(message):
                return
            self.bot.reply_to(
                message,
                "Используйте кнопки ниже или /start.",
                reply_markup=main_keyboard(is_tg_admin(message.from_user.id)),
            )

    def handle(self, *args, **options):
        self.stdout.write("Бот запущен...")
        while True:
            try:
                self.bot.polling(none_stop=True, timeout=30)
            except Exception as e:
                self.stderr.write(f"Ошибка polling: {e}")
                continue
                