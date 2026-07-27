from django.http import JsonResponse

from .services import list_active_items, resolve_dynamic_helper


def item_list(request):
    items, external_status = list_active_items()
    if request.GET.get("dynamic"):
        resolve_dynamic_helper(request.GET["dynamic"])
    return JsonResponse({"query": "active-items", "external_status": external_status["name"]})
