from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from .models import Client
from .services import normalize_phone, accrue, write_off


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
    operations = client.operations.order_by("-created_at")
    return render(request, "loyalty_programm/client.html",
                  {"client": client, "operations": operations})


@login_required
def client_operation(request, pk, op_type):
    client = get_object_or_404(Client, pk=pk)
    if op_type not in ("accrual", "write_off"):
        return redirect("client", pk=pk)

    if request.method == "POST":
        try:
            amount = int(request.POST.get("amount", "0"))
        except ValueError:
            messages.error(request, "Сумма должна быть целым числом")
            return render(request, "loyalty_programm/operation.html",
                          {"client": client, "op_type": op_type})
        comment = request.POST.get("comment", "").strip()
        try:
            if op_type == "accrual":
                accrue(client, amount, request.user, comment)
                messages.success(request, f"Начислено {amount} бонусов")
            else:
                write_off(client, amount, request.user, comment)
                messages.success(request, f"Списано {amount} бонусов")
        except ValidationError as e:
            messages.error(request, e.message)
            return render(request, "loyalty_programm/operation.html",
                          {"client": client, "op_type": op_type})
        return redirect("client", pk=pk)

    return render(request, "loyalty_programm/operation.html",
                  {"client": client, "op_type": op_type})


@login_required
def client_toggle_admin(request, pk):
    client = get_object_or_404(Client, pk=pk)
    client.is_bot_admin = not client.is_bot_admin
    client.save(update_fields=["is_bot_admin"])
    state = "админ" if client.is_bot_admin else "обычный клиент"
    messages.success(request, f"{client.name} теперь: {state}")
    return redirect("client", pk=pk)