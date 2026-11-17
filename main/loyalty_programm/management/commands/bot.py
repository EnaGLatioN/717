import html

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from django.core.management.base import BaseCommand
from django.conf import settings

from loyalty_programm.models import Client, Visit
from loyalty_programm.services import (
    get_or_create_client, format_phone, is_tg_admin,
)

from . import admin_handlers


def esc(text) -> str:
    """Экранирует текст для Telegram HTML parse_mode."""
    return html.escape(str(text if text is not None else ""), quote=False)


def main_keyboard(client: Client | None = None, is_admin: bool = False):
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

    def _render_client_card(self, client: Client) -> str:
        next_level, left = client.next_level_info()          # ← скобки!
        lines = [
            f"👤 <b>{esc(client.name)}</b>",
            f"📞 {esc(format_phone(client.phone))}",
            "",
            f"💳 Бонусы: <b>{client.balance}</b>",
            f"⏱ Минуты абонемента: <b>{client.minutes}</b>",
            f"🏅 Уровень: <b>{esc(client.get_level_display())}</b> "
            f"({client.cashback_percent}% кэшбека)",
        ]
        if next_level:
            level_label = dict(Client.LEVEL_CHOICES)[next_level]
            lines.append(f"📈 Следующий уровень «{esc(level_label)}».")
        else:
            lines.append("🏆 Максимальный уровень")
        lines.append("")
        lines.append("Покажите этот экран сотруднику.")
        return "\n".join(lines)

    def setup_handlers(self):

        @self.bot.message_handler(commands=["start"])
        def on_start(message):
            tg_id = message.from_user.id
            name = message.from_user.first_name or "Гость"
            client = Client.objects.filter(telegram_id=tg_id).first()

            if client and client.is_blocked and not is_tg_admin(tg_id):
                self._edit_or_send(message, "🚫 Ваш доступ заблокирован.")
                return

            if client:
                self._edit_or_send(
                    message, self._render_client_card(client),
                    reply_markup=self._kb_for(tg_id),
                )
                return

            kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.add(KeyboardButton("📱 Поделиться контактом", request_contact=True))
            self._edit_or_send(
                message,
                f"👋 <b>{esc(name)}</b>, добро пожаловать в программу лояльности!\n\n"
                f"Нажмите «Поделиться контактом», чтобы зарегистрироваться.",
                reply_markup=kb,
            )

        @self.bot.message_handler(commands=["admin"])
        def on_admin(message):
            if not is_tg_admin(message.from_user.id):
                self._edit_or_send(
                    message, "⛔ Нет доступа.",
                    reply_markup=self._kb_for(message.from_user.id),
                )
                return
            self.ctx.show_admin_menu(message.chat.id)

        @self.bot.message_handler(func=lambda m: m.text == "🛠 Админ-панель")
        def on_admin_button(message):
            if not is_tg_admin(message.from_user.id):
                self._edit_or_send(
                    message, "⛔ Нет доступа.",
                    reply_markup=self._kb_for(message.from_user.id),
                )
                return
            self.ctx.show_admin_menu(message.chat.id)

        @self.bot.message_handler(content_types=["contact"])
        def on_contact(message):
            contact = message.contact
            tg_id = message.from_user.id

            existing = Client.objects.filter(telegram_id=tg_id).first()
            if existing and existing.phone:
                self._edit_or_send(
                    message,
                    f"ℹ️ У вас уже привязан номер "
                    f"<b>{esc(format_phone(existing.phone))}</b>.\n"
                    f"Если нужно изменить — обратитесь к администратору.\n\n"
                    + self._render_client_card(existing),
                    reply_markup=self._kb_for(tg_id),
                )
                return

            if contact.user_id != message.from_user.id:
                self._edit_or_send(
                    message, "Пожалуйста, поделитесь именно своим контактом.",
                    reply_markup=self._kb_for(tg_id),
                )
                return

            name = " ".join(filter(None, [
                message.from_user.first_name, message.from_user.last_name,
            ])) or "Гость"

            try:
                client, created, conflict = get_or_create_client(
                    telegram_id=tg_id, name=name, phone=contact.phone_number,
                )
            except Exception as e:
                self._edit_or_send(
                    message, f"Не удалось сохранить номер: {esc(e)}",
                    reply_markup=self._kb_for(tg_id),
                )
                return

            if conflict is not None:
                self._edit_or_send(
                    message,
                    f"❌ <b>Этот номер уже привязан к другому пользователю.</b>\n\n"
                    f"📞 {esc(format_phone(conflict.phone))}\n\n"
                    f"Регистрация невозможна. Если это ошибка — обратитесь к администратору.",
                    reply_markup=self._kb_for(tg_id),
                )
                return

            self._edit_or_send(
                message,
                "✅ Регистрация завершена!\n\n" + self._render_client_card(client),
                reply_markup=self._kb_for(tg_id),
            )

        @self.bot.message_handler(func=lambda m: m.text == "💳 Мой баланс")
        def on_balance(message):
            client = Client.objects.filter(telegram_id=message.from_user.id).first()
            if not client:
                self._edit_or_send(
                    message, "Сначала зарегистрируйтесь: /start",
                    reply_markup=self._kb_for(message.from_user.id),
                )
                return
            if client.is_blocked and not is_tg_admin(message.from_user.id):
                self._edit_or_send(message, "🚫 Ваш доступ заблокирован.")
                return
            self._edit_or_send(
                message, self._render_client_card(client),
                reply_markup=self._kb_for(message.from_user.id),
            )

        @self.bot.message_handler(func=lambda m: m.text == "📜 История")
        def on_history(message):
            client = Client.objects.filter(telegram_id=message.from_user.id).first()
            if not client:
                self._edit_or_send(
                    message, "Сначала зарегистрируйтесь: /start",
                    reply_markup=self._kb_for(message.from_user.id),
                )
                return
            if client.is_blocked and not is_tg_admin(message.from_user.id):
                self._edit_or_send(message, "🚫 Ваш доступ заблокирован.")
                return

            visits = list(client.visits.order_by("-created_at")[:10])
            grants = list(client.minute_grants.order_by("-created_at")[:5])

            if not visits and not grants:
                self._edit_or_send(
                    message, "Операций пока нет.",
                    reply_markup=self._kb_for(message.from_user.id),
                )
                return

            lines = []
            if visits:
                lines.append("📜 <b>Последние визиты:</b>\n")
                for v in visits:
                    dt = v.created_at.strftime("%d.%m.%Y")
                    if v.status == Visit.STATUS_CANCELLED:
                        lines.append(f"{dt}  <s>{v.price} ₽</s>  <i>отменён</i>")
                        continue
                    parts = [f"{dt}", f"{v.price} ₽"]
                    if v.bonuses_used:
                        parts.append(f"−{v.bonuses_used} бон.")
                    if v.minutes_used:
                        parts.append(f"−{v.minutes_used} мин.")
                    parts.append(f"+{v.cashback} бон.")
                    if v.comment:
                        parts.append(f"— {esc(v.comment)}")
                    lines.append("  ".join(parts))

            if grants:
                lines.append("\n⏱ <b>Назначения минут:</b>\n")
                for g in grants:
                    dt = g.created_at.strftime("%d.%m.%Y")
                    cmt = f" — {esc(g.comment)}" if g.comment else ""
                    lines.append(f"{dt}  +{g.minutes} мин.{cmt}")

            lines.append(
                f"\n💳 Баланс: <b>{client.balance}</b> · "
                f"⏱ Минуты: <b>{client.minutes}</b>"
            )
            self._edit_or_send(
                message, "\n".join(lines),
                reply_markup=self._kb_for(message.from_user.id),
            )

        # регистрируем весь админ-функционал
        admin_handlers.register(self.bot, self.ctx)

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
