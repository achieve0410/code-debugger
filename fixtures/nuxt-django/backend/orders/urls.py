from django.urls import path

from . import views

acl_urlpatterns = [
    path("", views.acl_list, name="acl-list"),
]

urlpatterns = [
    path("items/", views.item_list, name="item-list"),
    path("orders/", views.order_collection, name="order-collection"),
    path("orders/<int:pk>/", views.order_detail, name="order-detail"),
    path("orders/<int:pk>/cancel/", views.cancel_order, name="order-cancel"),
]
