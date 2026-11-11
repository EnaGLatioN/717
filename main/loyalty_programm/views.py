from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from .models import Client, Visit, MinuteGrant
from .services import (
    normalize_phone, preview_visit, conduct_visit, cancel_visit, grant_minutes,
)


@login_required
def search(request):
    query = request.GET.get("q", "").strip()
    client = None
    if query:
        try:
            phone = normalize_phone(query)
            client = Client.objects.filter(phone=phone).first()
        except ValidationError:
            client = None
        if not client:
            messages.error(request, f"Клиент с номером {query} не найден")
    return render(request, "loyalty_programm/search.html",
                  {"query": query, "client": client})


@login_required
def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    visits = client.visits.order_by("-created_at")
    grants = client.minute_grants.order_by("-created_at")
    return render(request, "loyalty_programm/client.html", {
        "client": client, "visits": visits, "grants": grants,
        "next_level": client.next_level_info(),
    })


@login_required
def visit_create(request, pk):
    """Форма визита. GET — форма; POST — саммари для подтверждения."""
    client = get_object_or_404(Client, pk=pk)

    if request.method == "POST" and request.POST.get("confirm") != "1":
        # шаг 1 — расчёт и показ саммари
        try:
            price = int(request.POST.get("price", "0"))
            bonuses = int(request.POST.get("bonuses_used", "0"))
            minutes = int(request.POST.get("minutes_used", "0"))
        except ValueError:
            messages.error(request, "Цена, бонусы и минуты должны быть целыми числами")
            return render(request, "loyalty_programm/visit_form.html", {"client": client})
        comment = request.POST.get("comment", "").strip()
        try:
            summary = preview_visit(client, price, bonuses, minutes)
        except ValidationError as e:
            messages.error(request, e.message)
            return render(request, "loyalty_programm/visit_form.html", {
                "client": client, "form": request.POST,
            })
        return render(request, "loyalty_programm/visit_confirm.html", {
            "client": client, "summary": summary, "comment": comment,
            "form": request.POST,
        })

    if request.method == "POST" and request.POST.get("confirm") == "1":
        # шаг 2 — подтверждено, пишем в БД
        try:
            price = int(request.POST.get("price", "0"))
            bonuses = int(request.POST.get("bonuses_used", "0"))
            minutes = int(request.POST.get("minutes_used", "0"))
        except ValueError:
            messages.error(request, "Некорректные данные")
            return redirect("client", pk=pk)
        comment = request.POST.get("comment", "").strip()
        try:
            visit = conduct_visit(client, price, bonuses, minutes,
                                  admin=request.user, comment=comment)
        except ValidationError as e:
            messages.error(request, e.message)
            return redirect("client", pk=pk)
        messages.success(request, f"Визит сохранён: {visit.price} ₽, "
                                   f"кэшбек {visit.cashback} бонусов")
        return redirect("client", pk=pk)

    return render(request, "loyalty_programm/visit_form.html", {"client": client})


@login_required
def visit_cancel(request, pk, visit_pk):
    client = get_object_or_404(Client, pk=pk)
    visit = get_object_or_404(Visit, pk=visit_pk, client=client)

    if request.method == "POST":
        try:
            cancel_visit(visit)
            messages.success(request, "Визит отменён, балансы восстановлены")
        except ValidationError as e:
            messages.error(request, e.message)
        return redirect("client", pk=pk)

    return render(request, "loyalty_programm/visit_cancel.html", {
        "client": client, "visit": visit,
    })


@login_required
def grant_minutes_view(request, pk):
    client = get_object_or_404(Client, pk=pk)

    if request.method == "POST":
        try:
            minutes = int(request.POST.get("minutes", "0"))
        except ValueError:
            messages.error(request, "Минуты должны быть целым числом")
            return render(request, "loyalty_programm/grant_minutes.html", {"client": client})
        comment = request.POST.get("comment", "").strip()
        try:
            grant_minutes(client, minutes, admin=request.user, comment=comment)
            messages.success(request, f"Назначено {minutes} минут")
        except ValidationError as e:
            messages.error(request, e.message)
            return render(request, "loyalty_programm/grant_minutes.html", {"client": client})
        return redirect("client", pk=pk)

    return render(request, "loyalty_programm/grant_minutes.html", {"client": client})


@login_required
def client_toggle_admin(request, pk):
    client = get_object_or_404(Client, pk=pk)
    client.is_bot_admin = not client.is_bot_admin
    client.save(update_fields=["is_bot_admin"])
    state = "админ" if client.is_bot_admin else "обычный клиент"
    messages.success(request, f"{client.name} теперь: {state}")
    return redirect("client", pk=pk)