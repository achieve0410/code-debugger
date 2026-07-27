from django.urls import path

from . import views

urlpatterns = [
    path("api/items/", views.item_list, name="item-list"),
]
