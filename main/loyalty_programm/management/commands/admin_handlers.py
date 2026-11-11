"""
Админ-функционал Telegram-бота (полный, под новое ТЗ).

Всё «навигационное» (меню, карточка, поиск, история, статус, recent, stats)
редактирует ОДНО живое сообщение в чате. Результаты операций — короткий
push (ephemeral), удаляется при следующем действии.

Проведение визита — 2 шага: форма → саммари → подтверждение.
Сохранение только по кнопке «Подтвердить».
"""
import html

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from django.core.exceptions import ValidationError

from loyalty_programm.models import Client, Visit, MinuteGrant
from loyalty_programm.services import (
    normalize_phone, format_phone,
    preview_visit, conduct_visit, cancel_visit, grant_minutes,
    change_status, is_tg_admin,
)


def esc(text) -> str:
    """Экранирует текст для Telegram HTML parse_mode."""
    return html.escape(str(text if text is not None else ""), quote=False)


class AdminContext:
    def __init__(self, bot):
        self.bot = bot
        self.live_msg: dict[int, int] = {}
        self.ephemeral_msg: dict[int, int] = {}
        self.state: dict[int, dict] = {}

    # ---------- helpers ----------

    def _edit(self, chat_id, text, reply_markup=None):
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

    def _reset_state(self, chat_id):
        self.state.pop(chat_id, None)

    # ---------- меню ----------

    def show_admin_menu(self, chat_id):
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
        self._reset_state(chat_id)
        self._edit(chat_id, text, reply_markup=kb)

    # ---------- поиск ----------

    def ask_search(self, chat_id):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ Отмена", callback_data="adm_menu"))
        self._edit(chat_id, "🔎 Введите номер телефона клиента:", reply_markup=kb)
        self.state[chat_id] = {"step": "search_phone", "data": {}}

    def do_search(self, chat_id, raw_phone):
        try:
            phone = normalize_phone(raw_phone)
        except ValidationError:
            self._edit(chat_id, "❌ Не похоже на номер телефона. Введите ещё раз:")
            return
        client = Client.objects.filter(phone=phone).first()
        if not client:
            self._edit(chat_id, "❌ Клиент не найден. Введите другой номер:")
            return
        self._reset_state(chat_id)
        self.show_client_card(chat_id, client)

    # ---------- карточка ----------

    def show_client_card(self, chat_id, client: Client):
        next_level, left = client.next_level_info()          # ← скобки!
        admin_mark = " ✅" if client.is_bot_admin else ""
        block_mark = " 🚫" if client.is_blocked else ""

        lines = [
            f"👤 <b>{esc(client.name)}</b>{admin_mark}{block_mark}",
            f"📞 {esc(format_phone(client.phone))}",
            f"🆔 TG: <code>{client.telegram_id}</code>",
            "",
            f"💳 Бонусы: <b>{client.balance}</b>",
            f"⏱ Минуты: <b>{client.minutes}</b>",
            f"🏅 Уровень: <b>{esc(client.get_level_display())}</b> "
            f"({client.cashback_percent}% кэшбека)",
            f"💰 Оплачено деньгами: <b>{client.total_paid} ₽</b>",
        ]
        if next_level:
            level_label = dict(Client.LEVEL_CHOICES)[next_level]
            lines.append(
                f"📈 До «{esc(level_label)}»: <b>{left} ₽</b>"
            )
        else:
            lines.append("🏆 Максимальный уровень")
        lines.append("")
        lines.append(f"🏷 Статус: {esc(client.get_status_display())}")
        lines.append(f"📅 С нами с: {client.created_at.strftime('%d.%m.%Y')}")

        visits = list(client.visits.order_by("-created_at")[:5])
        if visits:
            lines.append("\n<b>Последние визиты:</b>")
            for v in visits:
                dt = v.created_at.strftime("%d.%m.%Y")
                if v.status == Visit.STATUS_CANCELLED:
                    lines.append(f"{dt}  <s>{v.price}₽</s> <i>отменён</i>")
                else:
                    lines.append(
                        f"{dt}  {v.price}₽ −{v.bonuses_used}б "
                        f"−{v.minutes_used}м +{v.cashback}б"
                    )

        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🧾 Провести визит", callback_data=f"adm_visit_{client.pk}"),
            InlineKeyboardButton("⏱ Назначить минуты", callback_data=f"adm_min_{client.pk}"),
        )
        kb.add(
            InlineKeyboardButton("📜 История", callback_data=f"adm_hist_{client.pk}"),
            InlineKeyboardButton("🏷 Статус", callback_data=f"adm_status_{client.pk}"),
        )
        kb.add(InlineKeyboardButton("🔙 В меню", callback_data="adm_menu"))
        self._edit(chat_id, "\n".join(lines), reply_markup=kb)

    # ---------- ВИЗИТ: шаг 1 — цена ----------

    def ask_visit_price(self, chat_id, client_pk):
        try:
            client = Client.objects.get(pk=client_pk)
        except Client.DoesNotExist:
            self._edit(chat_id, "❌ Клиент не найден.")
            return
        self._edit(
            chat_id,
            f"🧾 <b>Визит — {esc(client.name)}</b>\n"
            f"Баланс: {client.balance} бон. · Минуты: {client.minutes}\n"
            f"Уровень: {esc(client.get_level_display())} "
            f"({client.cashback_percent}%)\n\n"
            f"Введите <b>цену визита</b> в ₽ (целое число):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Отмена", callback_data=f"adm_view_{client.pk}"),
            ),
        )
        self.state[chat_id] = {
            "step": "visit_price",
            "data": {"client_pk": client.pk},
        }

    def do_visit_price(self, chat_id, raw):
        st = self.state.get(chat_id) or {}
        client_pk = st.get("data", {}).get("client_pk")
        if not client_pk:
            self._edit(chat_id, "Сессия истекла.")
            self._reset_state(chat_id)
            return
        if not raw.isdigit() or int(raw) < 0:
            self._edit(chat_id, "❌ Нужно целое число ≥ 0. Введите цену:")
            return
        price = int(raw)
        client = Client.objects.get(pk=client_pk)
        max_bonus = price // 2

        self._edit(
            chat_id,
            f"Цена: <b>{price} ₽</b>\n"
            f"Можно списать бонусов: до <b>{min(max_bonus, client.balance)}</b> "
            f"(половина чека, но не больше баланса {client.balance}).\n\n"
            f"Введите <b>бонусы к списанию</b> (или «0»):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("➡️ 0", callback_data="adm_visit_zero_bonus"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"adm_view_{client.pk}"),
            ),
        )
        st["step"] = "visit_bonuses"
        st["data"]["price"] = price
        self.state[chat_id] = st

    def do_visit_bonuses(self, chat_id, raw):
        st = self.state.get(chat_id) or {}
        data = st.get("data", {})
        client_pk = data.get("client_pk")
        if not client_pk or "price" not in data:
            self._edit(chat_id, "Сессия истекла.")
            self._reset_state(chat_id)
            return
        if not raw.isdigit():
            self._edit(chat_id, "❌ Нужно целое число ≥ 0. Введите бонусы:")
            return
        bonuses = int(raw)
        client = Client.objects.get(pk=client_pk)

        self._edit(
            chat_id,
            f"Цена: <b>{data['price']} ₽</b> · Бонусы: <b>{bonuses}</b>\n"
            f"Остаток минут у клиента: <b>{client.minutes}</b>\n\n"
            f"Введите <b>минуты к списанию</b> (или «0»):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("➡️ 0", callback_data="adm_visit_zero_minutes"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"adm_view_{client.pk}"),
            ),
        )
        st["step"] = "visit_minutes"
        st["data"]["bonuses"] = bonuses
        self.state[chat_id] = st

    def do_visit_minutes(self, chat_id, raw):
        st = self.state.get(chat_id) or {}
        data = st.get("data", {})
        client_pk = data.get("client_pk")
        if not client_pk or "bonuses" not in data:
            self._edit(chat_id, "Сессия истекла.")
            self._reset_state(chat_id)
            return
        if not raw.isdigit():
            self._edit(chat_id, "❌ Нужно целое число ≥ 0. Введите минуты:")
            return
        minutes = int(raw)
        client = Client.objects.get(pk=client_pk)

        # валидируем на лету
        try:
            preview_visit(client, data["price"], data["bonuses"], minutes)
        except ValidationError as e:
            self._edit(chat_id, f"❌ {esc(e.message)}\n\nВведите минуты ещё раз:")
            return

        self._edit(
            chat_id,
            f"Цена: <b>{data['price']} ₽</b> · Бонусы: <b>{data['bonuses']}</b> "
            f"· Минуты: <b>{minutes}</b>\n\n"
            f"Введите <b>комментарий</b> (или «-», чтобы пропустить):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("➡️ Пропустить", callback_data="adm_visit_skip_comment"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"adm_view_{client.pk}"),
            ),
        )
        st["step"] = "visit_comment"
        st["data"]["minutes"] = minutes
        self.state[chat_id] = st

    # ---------- ВИЗИТ: шаг 2 — саммари и подтверждение ----------

    def show_visit_confirm(self, chat_id, comment=""):
        st = self.state.get(chat_id) or {}
        data = st.get("data", {})
        client_pk = data.get("client_pk")
        try:
            price = data["price"]; bonuses = data["bonuses"]; minutes = data["minutes"]
        except KeyError:
            self._edit(chat_id, "Сессия истекла.")
            self._reset_state(chat_id)
            return
        client = Client.objects.get(pk=client_pk)
        try:
            s = preview_visit(client, price, bonuses, minutes)
        except ValidationError as e:
            self._edit(chat_id, f"❌ {esc(e.message)}")
            self._reset_state(chat_id)
            return

        level_up_mark = ""
        if s["level_after"] != s["level_before"]:
            level_up_mark = (
                f"\n🎉 После визита: <b>"
                f"{esc(dict(Client.LEVEL_CHOICES)[s['level_after']])}</b>"
            )

        text = (
            f"🧾 <b>Проверьте визит</b>\n\n"
            f"👤 {esc(client.name)} · {esc(format_phone(client.phone))}\n\n"
            f"Цена визита:        <b>{s['price']} ₽</b>\n"
            f"К оплате деньгами:  <b>{s['money_paid']} ₽</b>\n"
            f"Минуты к списанию:  <b>{s['minutes_used']}</b> "
            f"(останется {s['minutes_after']})\n"
            f"Бонусы к списанию:  <b>{s['bonuses_used']}</b>\n"
            f"Процент кэшбека:    <b>{s['cashback_percent']}%</b>\n"
            f"Бонусов начислится: <b>+{s['cashback']}</b>\n"
            f"Бонусы после:       <b>{s['balance_after']}</b> "
            f"(было {s['balance_before']})\n"
            f"Оплачено деньгами:  <b>{s['total_paid_after']} ₽</b> "
            f"(было {s['total_paid_before']})"
            f"{level_up_mark}\n\n"
            f"Комментарий: {esc(comment) or '—'}"
        )
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Подтвердить", callback_data="adm_visit_confirm"),
            InlineKeyboardButton("✏️ Изменить", callback_data=f"adm_visit_{client.pk}"),
        )
        kb.add(InlineKeyboardButton("❌ Отмена", callback_data=f"adm_view_{client.pk}"))
        self._edit(chat_id, text, reply_markup=kb)
        st["step"] = "visit_confirm"
        st["data"]["comment"] = comment
        self.state[chat_id] = st

    def apply_visit(self, chat_id, admin_tg_id):
        st = self.state.get(chat_id) or {}
        data = st.get("data", {})
        client_pk = data.get("client_pk")
        try:
            price = data["price"]; bonuses = data["bonuses"]
            minutes = data["minutes"]; comment = data.get("comment", "")
        except KeyError:
            self._edit(chat_id, "Сессия истекла.")
            self._reset_state(chat_id)
            return
        try:
            client = Client.objects.get(pk=client_pk)
            visit = conduct_visit(
                client, price, bonuses, minutes,
                admin=None, comment=comment, performed_by_tg=admin_tg_id,
            )
        except Client.DoesNotExist:
            self._edit(chat_id, "❌ Клиент не найден.")
            self._reset_state(chat_id)
            return
        except ValidationError as e:
            self._edit(chat_id, f"❌ {esc(e.message)}")
            self._reset_state(chat_id)
            return

        self._reset_state(chat_id)
        client.refresh_from_db()
        self._push(
            chat_id,
            f"✅ Визит сохранён\n"
            f"Кэшбек: <b>+{visit.cashback}</b> бон.\n"
            f"Баланс: <b>{client.balance}</b> · Минуты: <b>{client.minutes}</b>",
        )
        self.show_client_card(chat_id, client)

    # ---------- ОТМЕНА ВИЗИТА ----------

    def ask_cancel_visit(self, chat_id, client_pk, visit_pk):
        try:
            client = Client.objects.get(pk=client_pk)
            visit = Visit.objects.get(pk=visit_pk, client=client)
        except (Client.DoesNotExist, Visit.DoesNotExist):
            self._edit(chat_id, "❌ Визит не найден.")
            return
        if visit.status == Visit.STATUS_CANCELLED:
            self._edit(chat_id, "Визит уже отменён.")
            return
        text = (
            f"❌ <b>Отменить визит?</b>\n\n"
            f"👤 {esc(client.name)}\n"
            f"📅 {visit.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Цена:     <b>{visit.price} ₽</b>\n"
            f"Бонусы:   −{visit.bonuses_used} / +{visit.cashback}\n"
            f"Минуты:   −{visit.minutes_used}\n"
            f"Деньги:   <b>{visit.money_paid} ₽</b>\n\n"
            f"Балансы и сумма оплат вернутся, уровень пересчитается."
        )
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Да, отменить",
                                 callback_data=f"adm_visit_cancel_{visit.pk}"),
            InlineKeyboardButton("🔙 Нет", callback_data=f"adm_view_{client.pk}"),
        )
        self._edit(chat_id, text, reply_markup=kb)

    def do_cancel_visit(self, chat_id, visit_pk):
        try:
            visit = Visit.objects.select_related("client").get(pk=visit_pk)
        except Visit.DoesNotExist:
            self._edit(chat_id, "❌ Визит не найден.")
            return
        client = visit.client
        try:
            cancel_visit(visit)
        except ValidationError as e:
            self._edit(chat_id, f"❌ {esc(e.message)}")
            return
        client.refresh_from_db()
        self._push(chat_id, "✅ Визит отменён, балансы восстановлены.")
        self.show_client_card(chat_id, client)

    # ---------- НАЗНАЧЕНИЕ МИНУТ ----------

    def ask_minutes(self, chat_id, client_pk):
        try:
            client = Client.objects.get(pk=client_pk)
        except Client.DoesNotExist:
            self._edit(chat_id, "❌ Клиент не найден.")
            return
        self._edit(
            chat_id,
            f"⏱ <b>Назначить минуты</b>\n\n"
            f"👤 {esc(client.name)}\n"
            f"Остаток: <b>{client.minutes}</b>\n\n"
            f"Введите <b>количество минут</b> (целое > 0):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Отмена", callback_data=f"adm_view_{client.pk}"),
            ),
        )
        self.state[chat_id] = {
            "step": "grant_minutes",
            "data": {"client_pk": client.pk},
        }

    def do_grant_minutes(self, chat_id, admin_tg_id, raw):
        st = self.state.get(chat_id) or {}
        client_pk = st.get("data", {}).get("client_pk")
        if not client_pk:
            self._edit(chat_id, "Сессия истекла.")
            self._reset_state(chat_id)
            return
        if not raw.isdigit() or int(raw) <= 0:
            self._edit(chat_id, "❌ Нужно целое > 0. Введите минуты:")
            return
        minutes = int(raw)
        try:
            client = Client.objects.get(pk=client_pk)
            grant_minutes(client, minutes, admin=None, performed_by_tg=admin_tg_id)
        except ValidationError as e:
            self._edit(chat_id, f"❌ {esc(e.message)}")
            self._reset_state(chat_id)
            return
        self._reset_state(chat_id)
        client.refresh_from_db()
        self._push(
            chat_id,
            f"✅ Назначено <b>+{minutes}</b> мин.\n"
            f"Теперь минут: <b>{client.minutes}</b>",
        )
        self.show_client_card(chat_id, client)

    # ---------- ИСТОРИЯ ----------

    def show_history(self, chat_id, client_pk):
        try:
            client = Client.objects.get(pk=client_pk)
        except Client.DoesNotExist:
            self._edit(chat_id, "❌ Клиент не найден.")
            return

        visits = list(client.visits.order_by("-created_at")[:20])
        grants = list(client.minute_grants.order_by("-created_at")[:10])

        lines = [f"📜 <b>История — {esc(client.name)}</b>\n"]
        if visits:
            lines.append("<b>Визиты:</b>")
            for v in visits:
                dt = v.created_at.strftime("%d.%m.%Y")
                if v.status == Visit.STATUS_CANCELLED:
                    lines.append(f"{dt}  <s>{v.price}₽</s> <i>отменён</i>")
                else:
                    cmt = f" — {esc(v.comment)}" if v.comment else ""
                    lines.append(
                        f"{dt}  {v.price}₽ −{v.bonuses_used}б "
                        f"−{v.minutes_used}м +{v.cashback}б{cmt}"
                    )
        else:
            lines.append("— визитов нет —")

        if grants:
            lines.append("\n<b>Назначения минут:</b>")
            for g in grants:
                dt = g.created_at.strftime("%d.%m.%Y")
                cmt = f" — {esc(g.comment)}" if g.comment else ""
                lines.append(f"{dt}  +{g.minutes} мин.{cmt}")

        kb = InlineKeyboardMarkup(row_width=2)
        active_visits = [v for v in visits if v.status == Visit.STATUS_DONE]
        for v in active_visits[:5]:
            kb.add(InlineKeyboardButton(
                f"❌ Отменить {v.created_at.strftime('%d.%m')} — {v.price}₽",
                callback_data=f"adm_cancel_ask_{v.pk}",
            ))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data=f"adm_view_{client.pk}"))
        self._edit(chat_id, "\n".join(lines), reply_markup=kb)

    # ---------- СТАТУС ----------

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
            f"🏷 <b>{esc(client.name)}</b>\n"
            f"Текущий статус: {esc(client.get_status_display())}\n\n"
            f"Выберите новый статус:",
            reply_markup=kb,
        )

    def do_set_status(self, chat_id, client_pk, new_status):
        try:
            client = Client.objects.get(pk=client_pk)
            change_status(client, new_status)
            client.refresh_from_db()
            self._push(chat_id, f"✅ Статус: <b>{esc(client.get_status_display())}</b>")
            self.show_client_card(chat_id, client)
        except (Client.DoesNotExist, ValidationError):
            self._edit(chat_id, "❌ Не удалось сменить статус.")

    # ---------- ПОСЛЕДНИЕ / СВОДКА ----------

    def show_recent(self, chat_id):
        clients = Client.objects.order_by("-created_at")[:20]
        if not clients:
            self._edit(chat_id, "Клиентов пока нет.")
            return
        kb = InlineKeyboardMarkup(row_width=1)
        for c in clients:
            mark = " ✅" if c.is_bot_admin else ""
            block = " 🚫" if c.is_blocked else ""
            kb.add(InlineKeyboardButton(
                f"{c.name} — {format_phone(c.phone)} "
                f"[{c.get_level_display()} · {c.balance}б]{mark}{block}",
                callback_data=f"adm_view_{c.pk}",
            ))
        kb.add(InlineKeyboardButton("🔙 В меню", callback_data="adm_menu"))
        self._edit(chat_id, "👥 <b>Последние клиенты:</b>", reply_markup=kb)

    def show_stats(self, chat_id):
        total = Client.objects.count()
        admins = Client.objects.filter(is_bot_admin=True).count()
        total_balance = sum(c.balance for c in Client.objects.all())
        total_minutes = sum(c.minutes for c in Client.objects.all())
        total_paid = sum(c.total_paid for c in Client.objects.all())
        done = Visit.objects.filter(status=Visit.STATUS_DONE).count()
        cancelled = Visit.objects.filter(status=Visit.STATUS_CANCELLED).count()
        cashback = sum(v.cashback for v in Visit.objects.filter(status=Visit.STATUS_DONE))
        text = (
            f"📊 <b>Сводка</b>\n\n"
            f"👥 Клиентов: <b>{total}</b>\n"
            f"🛠 Админов бота: <b>{admins}</b>\n\n"
            f"💳 Сумма бонусов: <b>{total_balance}</b>\n"
            f"⏱ Сумма минут: <b>{total_minutes}</b>\n"
            f"💰 Оплачено деньгами: <b>{total_paid} ₽</b>\n\n"
            f"🧾 Визитов: <b>{done}</b> · отменено: <b>{cancelled}</b>\n"
            f"🎁 Начислено кэшбека: <b>{cashback}</b> бон."
        )
        self._edit(
            chat_id, text,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 В меню", callback_data="adm_menu"),
            ),
        )

    # ---------- ВВОД ТЕКСТА ----------

    def handle_text_input(self, message):
        chat_id = message.from_user.id
        st = self.state.get(chat_id)
        if not st:
            return False
        if not is_tg_admin(chat_id):
            self._reset_state(chat_id)
            return False

        text = (message.text or "").strip()
        step = st.get("step")

        if step == "search_phone":
            self.do_search(chat_id, text)
            return True
        if step == "visit_price":
            self.do_visit_price(chat_id, text)
            return True
        if step == "visit_bonuses":
            self.do_visit_bonuses(chat_id, text)
            return True
        if step == "visit_minutes":
            self.do_visit_minutes(chat_id, text)
            return True
        if step == "visit_comment":
            if text in ("-", ""):
                self.show_visit_confirm(chat_id, "")
            else:
                self.show_visit_confirm(chat_id, text[:255])
            return True
        if step == "grant_minutes":
            self.do_grant_minutes(chat_id, chat_id, text)
            return True
        return False


# ============================================================
#  Регистрация callback-хендлеров
# ============================================================
def register(bot, ctx: AdminContext, edit_or_send=None, send_ephemeral=None):

    def guard(call) -> bool:
        if not is_tg_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Нет доступа", show_alert=True)
            return False
        return True

    @bot.callback_query_handler(func=lambda c: c.data == "adm_menu")
    def _adm_menu(call):
        if not guard(call): return
        ctx.show_admin_menu(call.from_user.id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_search")
    def _adm_search(call):
        if not guard(call): return
        ctx.ask_search(call.from_user.id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_recent")
    def _adm_recent(call):
        if not guard(call): return
        ctx.show_recent(call.from_user.id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
    def _adm_stats(call):
        if not guard(call): return
        ctx.show_stats(call.from_user.id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_view_"))
    def _adm_view(call):
        if not guard(call): return
        pk = int(call.data.replace("adm_view_", ""))
        try:
            client = Client.objects.get(pk=pk)
        except Client.DoesNotExist:
            bot.answer_callback_query(call.id, "Клиент не найден", show_alert=True)
            return
        ctx.show_client_card(call.from_user.id, client)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_visit_")
                                       and c.data != "adm_visit_confirm"
                                       and c.data not in (
                                           "adm_visit_zero_bonus",
                                           "adm_visit_zero_minutes",
                                           "adm_visit_skip_comment",
                                       ))
    def _adm_visit(call):
        if not guard(call): return
        pk = int(call.data.replace("adm_visit_", ""))
        ctx.ask_visit_price(call.from_user.id, pk)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_visit_zero_bonus")
    def _adm_visit_zero_bonus(call):
        if not guard(call): return
        ctx.do_visit_bonuses(call.from_user.id, "0")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_visit_zero_minutes")
    def _adm_visit_zero_minutes(call):
        if not guard(call): return
        ctx.do_visit_minutes(call.from_user.id, "0")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_visit_skip_comment")
    def _adm_visit_skip_comment(call):
        if not guard(call): return
        ctx.show_visit_confirm(call.from_user.id, "")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "adm_visit_confirm")
    def _adm_visit_confirm(call):
        if not guard(call): return
        ctx.apply_visit(call.from_user.id, call.from_user.id)
        bot.answer_callback_query(call.id, "Сохранено")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_cancel_ask_"))
    def _adm_cancel_ask(call):
        if not guard(call): return
        visit_pk = int(call.data.replace("adm_cancel_ask_", ""))
        try:
            visit = Visit.objects.get(pk=visit_pk)
        except Visit.DoesNotExist:
            bot.answer_callback_query(call.id, "Визит не найден", show_alert=True)
            return
        ctx.ask_cancel_visit(call.from_user.id, visit.client_id, visit.pk)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_visit_cancel_"))
    def _adm_visit_cancel(call):
        if not guard(call): return
        visit_pk = int(call.data.replace("adm_visit_cancel_", ""))
        ctx.do_cancel_visit(call.from_user.id, visit_pk)
        bot.answer_callback_query(call.id, "Отменено")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_min_"))
    def _adm_min(call):
        if not guard(call): return
        pk = int(call.data.replace("adm_min_", ""))
        ctx.ask_minutes(call.from_user.id, pk)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_status_"))
    def _adm_status(call):
        if not guard(call): return
        pk = int(call.data.replace("adm_status_", ""))
        ctx.ask_status(call.from_user.id, pk)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_setstatus_"))
    def _adm_setstatus(call):
        if not guard(call): return
        parts = call.data.split("_")
        try:
            pk = int(parts[2]); status = parts[3]
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id, "Некорректные данные", show_alert=True)
            return
        ctx.do_set_status(call.from_user.id, pk, status)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_hist_"))
    def _adm_hist(call):
        if not guard(call): return
        pk = int(call.data.replace("adm_hist_", ""))
        ctx.show_history(call.from_user.id, pk)
        bot.answer_callback_query(call.id)
