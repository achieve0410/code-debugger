from django.db import models


class Invoice(models.Model):
    total = models.DecimalField(max_digits=10, decimal_places=2)
