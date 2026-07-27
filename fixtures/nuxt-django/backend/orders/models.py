from django.db import models


class Item(models.Model):
    name = models.CharField(max_length=100)


class Order(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, default="new")
