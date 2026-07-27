import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import Item, Order


@require_GET
def item_list(request):
    items = list(Item.objects.values("id", "name"))
    return JsonResponse(items, safe=False)


@require_http_methods(["GET", "POST"])
def order_collection(request):
    if request.method == "POST":
        payload = json.loads(request.body)
        order = Order.objects.create(item_id=payload["itemId"], quantity=payload["quantity"])
        return JsonResponse({"id": order.pk}, status=201)
    orders = list(Order.objects.values("id", "quantity", "status"))
    return JsonResponse(orders, safe=False)


@require_GET
def order_detail(request, pk):
    order = Order.objects.get(pk=pk)
    return JsonResponse({"id": order.pk, "status": order.status})


@require_POST
def cancel_order(request, pk):
    order = Order.objects.get(pk=pk)
    order.status = "cancelled"
    order.save()
    return JsonResponse({"status": order.status})
