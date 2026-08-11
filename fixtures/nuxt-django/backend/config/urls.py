from django.urls import include, path

from orders.urls import acl_urlpatterns

urlpatterns = [
    path("api/", include("orders.urls")),
    path("app/v1/acl_policy/", include((acl_urlpatterns, "acl_policy"))),
]
