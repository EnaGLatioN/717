"""
Админ-функционал бота. Регистрируется из bot.py.

Всё «навигационное» (меню, карточка, поиск, история, статус) —
редактирует ОДНО живое сообщение в чате.
Результаты операций (начислено/списано) — короткий push через ephemeral,
который удаляется при следующем действии.
"""
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from django.core.exceptions import ValidationError

from loyalty_programm.models import Client, Operation
from loyalty_programm.services import (
    normalize_phone, format_phone, accrue, write_off, change_status,
    is_tg_admin,
)


class AdminContext:
    def __init__(self, bot):
        self.bot = bot
        # { chat_id: message_id } — одно «живое» сообщение
        self.live_msg = {}
        # { chat_id: message_id } — временное (результат операции)
        self.ephemeral_msg = {}
        # { tg_id: {"step": "...", "data": {...}} }
        self.state = {}

    # ---------- helpers ----------

    def _edit(self, chat_id, text, reply_markup=None):
        """Редактирует живое сообщение. Если нельзя — шлёт новое и запоминает."""
        prev_id = self.live_msg.get(chat_id)
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
        self.live_msg[chat_id] = sent.message_id

    def _push(self, chat_id, text, reply_markup=None):
        """Короткое push-сообщение (результат операции)."""
        prev = self.ephemeral_msg.get(chat_id)
        if prev:
            try:
                self.bot.delete_message(chat_id, prev)
            except Exception:
                pass
        sent = self.bot.send_message(chat_id, text, reply_markup=reply_markup)
        self.ephemeral_msg[chat_id] = sent.message_id

    def _clear_ephemeral(self, chat_id):
        prev = self.ephemeral_msg.pop(chat_id, None)
        if prev:
            try:
                self.bot.delete_message(chat_id, prev)
            except Exception:
                pass

    # ---------- меню ----------

    def show_admin_menu(self, chat_id, message=None):
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🔎 Найти клиента", callback_data="adm_search"),
            InlineKeyboardButton("👥 Последние клиенты", callback_data="adm_recent"),
        )
        kb.add(InlineKeyboardButton("📊 Сводка", callback_data="adm_stats"))
        text = (
            "🛠 <b>Админ-панель</b>\n\n"
            "Выберите действие. Поиск — по номеру телефона "
            "(8…, +7…, 9…, с пробелами — всё поймём)."
        )
        self._clear_ephemeral(chat_id)
        self._edit(chat_id, text, reply_markup=kb)

    # ---------- поиск ----------

    def ask_search(self, chat_id):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ Отмена", callback_data="adm_menu"))
        self._edit(
            chat_id,
            "🔎 Введите номер телефона клиента:",
            reply_markup=kb,
        )
        self.state[chat_id] = {"step": "search_phone", "data": {}}

    def do_search(self, chat_id, raw_phone):
        try:
            phone = normalize_phone(raw_phone)
        except ValidationError:
            self._edit(chat_id, "❌ Не похоже на номер телефона.")
            self.state.pop(chat_id, None)
            return
        client = Client.objects.filter(phone=phone).first()
        if not client:
            self._edit(chat_id, "❌ Клиент не найден.")
            self.state.pop(chat_id, None)
            return
        self.state.pop(chat_id, None)
        self.show_client_card(chat_id, client)

    # ---------- карточка клиента ----------

    def show_client_card(self, chat_id, client: Client):
        ops = client.operations.order_by("-created_at")[:5]
        ops_lines = []
        for op in ops:
            sign = "+" if op.type == Operation.TYPE_ACCRUAL else "−"
            dt = op.created_at.strftime("%d.%m.%Y %H:%M")
            cmt = f" — {op.comment}" if op.comment else ""
            ops_lines.append(f"{dt}  <b>{sign}{op.amount}</b>{cmt}")
        ops_text = "\n".join(ops_lines) if ops_lines else "— операций нет —"
        admin_mark = " ✅" if client.is_bot_admin else ""

        text = (
            f"👤 <b>{client.name}</b>{admin_mark}\n"
            f"📞 {format_phone(client.phone)}\n"
            f"💳 Баланс: <b>{client.balance}</b>\n"
            f"🏷 Статус: <b>{client.get_status_display()}</b>\n"
            f"🆔 TG: <code>{client.telegram_id}</code>\n"
            f"📅 С нами с: {client.created_at.strftime('%d.%m.%Y')}\n\n"
            f"<b>Последние операции:</b>\n{ops_text}"
        )
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("➕ Начислить", callback_data=f"adm_acc_{client.pk}"),
            InlineKeyboardButton("➖ Списать", callback_data=f"adm_wo_{client.pk}"),
        )
        kb.add(
            InlineKeyboardButton("🏷 Сменить статус", callback_data=f"adm_status_{client.pk}"),
            InlineKeyboardButton("📜 Вся история", callback_data=f"adm_hist_{client.pk}"),
        )
        kb.add(InlineKeyboardButton("🔙 В меню", callback_data="adm_menu"))
        self._edit(chat_id, text, reply_markup=kb)

    # ---------- начисление / списание ----------

    def ask_amount(self, chat_id, client_pk, op_type):
        client = Client.objects.get(pk=client_pk)
        verb = "начисления" if op_type == "accrual" else "списания"
        self._edit(
            chat_id,
            f"💳 {client.name} ({format_phone(client.phone)})\n"
            f"Текущий баланс: <b>{client.balance}</b>\n\n"
            f"Введите <b>сумму {verb}</b> (целое число):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Отмена", callback_data="adm_menu"),
            ),
        )
        self.state[chat_id] = {
            "step": "amount",
            "data": {"client_pk": client_pk, "op_type": op_type},
        }

    def do_amount(self, chat_id, admin_tg_id, raw):
        st = self.state.get(chat_id, {})
        data = st.get("data", {})
        client_pk = data.get("client_pk")
        op_type = data.get("op_type")
        if not client_pk or op_type not in ("accrual", "write_off"):
            self._edit(chat_id, "Сессия истекла.")
            self.state.pop(chat_id, None)
            return
        if not raw.isdigit() or int(raw) <= 0:
            self._edit(chat_id, "❌ Нужно целое число > 0. Введите ещё раз:")
            return
        amount = int(raw)
        self.state[chat_id] = {
            "step": "comment",
            "data": {
                "client_pk": client_pk, "op_type": op_type, "amount": amount,
            },
        }
        self._edit(
            chat_id,
            f"Сумма: <b>{amount}</b>\n\n"
            f"Введите <b>комментарий</b> (или отправьте «-», чтобы пропустить):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("➡️ Пропустить", callback_data="adm_skip_comment"),
                InlineKeyboardButton("❌ Отмена", callback_data="adm_menu"),
            ),
        )

    def apply_operation(self, chat_id, admin_tg_id, comment):
        st = self.state.get(chat_id, {})
        data = st.get("data", {})
        client_pk = data.get("client_pk")
        op_type = data.get("op_type")
        amount = data.get("amount")
        if not all([client_pk, op_type, amount]):
            self._edit(chat_id, "Сессия истекла.")
            self.state.pop(chat_id, None)
            return
        try:
            client = Client.objects.get(pk=client_pk)
            if op_type == "accrual":
                accrue(client, amount, admin=None,
                       comment=comment, performed_by_tg=admin_tg_id)
                result = f"✅ Начислено <b>+{amount}</b>"
            else:
                write_off(client, amount, admin=None,
                          comment=comment, performed_by_tg=admin_tg_id)
                result = f"✅ Списано <b>−{amount}</b>"
        except Client.DoesNotExist:
            self._edit(chat_id, "❌ Клиент не найден.")
            self.state.pop(chat_id, None)
            return
        except ValidationError as e:
            self._edit(chat_id, f"❌ {e.message}")
            self.state.pop(chat_id, None)
            return

        self.state.pop(chat_id, None)
        client.refresh_from_db()
        self._push(chat_id, f"{result}\nБаланс: <b>{client.balance}</b>")
        self.show_client_card(chat_id, client)

    # ---------- смена статуса ----------

    def ask_status(self, chat_id, client_pk):
        client = Client.objects.get(pk=client_pk)
        kb = InlineKeyboardMarkup(row_width=1)
        for value, label in Client.STATUS_CHOICES:
            prefix = "✅ " if client.status == value else ""
            kb.add(InlineKeyboardButton(
                f"{prefix}{label}",
                callback_data=f"adm_setstatus_{client.pk}_{value}",
            ))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data=f"adm_view_{client.pk}"))
        self._edit(
            chat_id,
            f"🏷 <b>{client.name}</b>\nТекущий статус: {client.get_status_display()}\n\n"
            f"Выберите новый статус:",
            reply_markup=kb,
        )

    def do_set_status(self, chat_id, client_pk, new_status):
        try:
            client = Client.objects.get(pk=client_pk)
            change_status(client, new_status)
            client.refresh_from_db()
            self._push(chat_id, f"✅ Статус: <b>{client.get_status_display()}</b>")
            self.show_client_card(chat_id, client)
        except (Client.DoesNotExist, ValidationError):
            self._edit(chat_id, "❌ Не удалось сменить статус.")

    # ---------- история ----------

    def show_history(self, chat_id, client_pk):
        client = Client.objects.get(pk=client_pk)
        ops = client.operations.order_by("-created_at")[:30]
        if not ops:
            self._edit(chat_id, "Операций нет.")
            return
        lines = [f"📜 <b>История — {client.name}</b>\n"]
        for op in ops:
            sign = "+" if op.type == Operation.TYPE_ACCRUAL else "−"
            dt = op.created_at.strftime("%d.%m.%Y %H:%M")
            cmt = f" — {op.comment}" if op.comment else ""
            who = f" (tg:{op.performed_by_tg})" if op.performed_by_tg else ""
            lines.append(f"{dt}  <b>{sign}{op.amount}</b>{cmt}{who}")
        self._edit(
            chat_id, "\n".join(lines),
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Назад", callback_data=f"adm_view_{client.pk}"),
            ),
        )

    # ---------- последние клиенты ----------

    def show_recent(self, chat_id):
        clients = Client.objects.order_by("-created_at")[:20]
        if not clients:
            self._edit(chat_id, "Клиентов пока нет.")
            return
        kb = InlineKeyboardMarkup(row_width=1)
        for c in clients:
            mark = " ✅" if c.is_bot_admin else ""
            kb.add(InlineKeyboardButton(
                f"{c.name} — {format_phone(c.phone)} ({c.balance}){mark}",
                callback_data=f"adm_view_{c.pk}",
            ))
        kb.add(InlineKeyboardButton("🔙 В меню", callback_data="adm_menu"))
        self._edit(chat_id, "👥 <b>Последние клиенты:</b>", reply_markup=kb)

    # ---------- сводка ----------

    def show_stats(self, chat_id):
        total = Client.objects.count()
        admins = Client.objects.filter(is_bot_admin=True).count()
        total_balance = sum(c.balance for c in Client.objects.all())
        accrual = sum(o.amount for o in Operation.objects.filter(type=Operation.TYPE_ACCRUAL))
        write = sum(o.amount for o in Operation.objects.filter(type=Operation.TYPE_WRITE_OFF))
        text = (
            f"📊 <b>Сводка</b>\n\n"
            f"👥 Клиентов: <b>{total}</b>\n"
            f"🛠 Админов бота: <b>{admins}</b>\n"
            f"💳 Сумма балансов: <b>{total_balance}</b>\n"
            f"➕ Всего начислено: <b>{accrual}</b>\n"
            f"➖ Всего списано: <b>{write}</b>"
        )
        self._edit(
            chat_id, text,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 В меню", callback_data="adm_menu"),
            ),
        )

    # ---------- ввод текста ----------

    def handle_text_input(self, message):
        chat_id = message.from_user.id
        st = self.state.get(chat_id)
        if not st:
            return False
        if not is_tg_admin(chat_id):
            self.state.pop(chat_id, None)
            return False

        text = (message.text or "").strip()
        step = st.get("step")

        if step == "search_phone":
            self.do_search(chat_id, text)
            return True
        if step == "amount":
            self.do_amount(chat_id, chat_id, text)
            return True
        if step == "comment":
            if text in ("-", ""):
                self.apply_operation(chat_id, chat_id, "")
            else:
                self.apply_operation(chat_id, chat_id, text[:255])
            return True
        return False


# ============================================================
#  Регистрация callback-хендлеров
# ============================================================
def register(bot, ctx: AdminContext, edit_or_send, send_ephemeral):

    def guard(call) -> bool:
        if not is_tg_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Нет доступа", show_alert=True)
            return False
        return True

    @bot.callback_query_handler(func=lambda c: c.data == "adm_menu")
    def _adm_menu(call):
        if not guard(call):
            return
        ctx.state.pop(call.from_user.id, None)
        ctx.show_admin_menu(call.from_user.id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_search")
    def _adm_search(call):
        if not guard(call):
            return
        ctx.ask_search(call.from_user.id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_recent")
    def _adm_recent(call):
        if not guard(call):
            return
        ctx.show_recent(call.from_user.id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
    def _adm_stats(call):
        if not guard(call):
            return
        ctx.show_stats(call.from_user.id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_view_"))
    def _adm_view(call):
        if not guard(call):
            return
        pk = int(call.data.replace("adm_view_", ""))
        try:
            client = Client.objects.get(pk=pk)
        except Client.DoesNotExist:
            bot.answer_callback_query(call.id, "Клиент не найден", show_alert=True)
            return
        ctx.show_client_card(call.from_user.id, client)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_acc_"))
    def _adm_acc(call):
        if not guard(call):
            return
        pk = int(call.data.replace("adm_acc_", ""))
        ctx.ask_amount(call.from_user.id, pk, "accrual")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_wo_"))
    def _adm_wo(call):
        if not guard(call):
            return
        pk = int(call.data.replace("adm_wo_", ""))
        ctx.ask_amount(call.from_user.id, pk, "write_off")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_skip_comment")
    def _adm_skip_comment(call):
        if not guard(call):
            return
        ctx.apply_operation(call.from_user.id, call.from_user.id, "")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_status_"))
    def _adm_status(call):
        if not guard(call):
            return
        pk = int(call.data.replace("adm_status_", ""))
        ctx.ask_status(call.from_user.id, pk)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_setstatus_"))
    def _adm_setstatus(call):
        if not guard(call):
            return
        parts = call.data.split("_")
        try:
            pk = int(parts[2])
            status = parts[3]
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id, "Некорректные данные", show_alert=True)
            return
        ctx.do_set_status(call.from_user.id, pk, status)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_hist_"))
    def _adm_hist(call):
        if not guard(call):
            return
        pk = int(call.data.replace("adm_hist_", ""))
        ctx.show_history(call.from_user.id, pk)
        bot.answer_callback_query(call.id)