from django.db import models


class Item(models.Model):
    name = models.CharField(max_length=64)
    active = models.BooleanField(default=True)
