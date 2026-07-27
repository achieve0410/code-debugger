from django.urls import include, path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register(prefix="invoices", viewset=views.InvoiceViewSet, basename="invoice")

urlpatterns = [
    path("v1/", include(router.urls)),
]
