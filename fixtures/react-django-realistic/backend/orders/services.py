def notify_warehouse(order):
    payload = build_payload(order)
    return payload


def build_payload(order):
    return {"order": order.pk, "status": order.status}
