from django.urls import include, path

urlpatterns = [
    path("api/", include("orders.urls")),
    path("api/billing/", include("billing.urls")),
]
