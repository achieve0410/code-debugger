from rest_framework import generics, viewsets
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Customer, Order
from .services import notify_warehouse


class OrderListCreateView(generics.ListCreateAPIView):
    queryset = Order.objects.all()


class OrderDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Order.objects.select_related("customer")


@api_view(["POST"])
def cancel_order(request, pk):
    order = Order.objects.get(pk=pk)
    order.status = "cancelled"
    order.save()
    notify_warehouse(order)
    return Response({"status": order.status})


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
