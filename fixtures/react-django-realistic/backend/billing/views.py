from rest_framework import viewsets

from .models import Invoice


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Invoice.objects.all()
