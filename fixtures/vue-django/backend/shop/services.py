from .models import Item

EXTERNAL_STATUS_URL = "https://inventory.example.test/status"


def list_active_items():
    return Item.objects.filter(active=True), external_status_boundary()


def resolve_dynamic_helper(name):
    return getattr(Item.objects, name)


def external_status_boundary():
    return {"method": "GET", "url": EXTERNAL_STATUS_URL, "name": "inventory-status"}
