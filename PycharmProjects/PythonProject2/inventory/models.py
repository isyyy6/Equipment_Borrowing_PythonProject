from django.conf import settings
from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)  # archive flag

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class CatalogItem(models.Model):
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="items"
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)  # archive flag

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "name"], name="unique_item_per_category"
            )
        ]

    def __str__(self):
        return self.name

    @property
    def serviceable_count(self):
        """Units that could be lent: not in maintenance, not retired."""
        return self.units.exclude(
            status__in=[EquipmentUnit.Status.MAINTENANCE, EquipmentUnit.Status.RETIRED]
        ).count()

    @property
    def available_count(self):
        return self.units.filter(status=EquipmentUnit.Status.AVAILABLE).count()


class EquipmentUnit(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        RESERVED = "RESERVED", "Reserved"
        ISSUED = "ISSUED", "Issued"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        RETIRED = "RETIRED", "Retired"

    class Condition(models.TextChoices):
        GOOD = "GOOD", "Good"
        FAIR = "FAIR", "Fair"
        DAMAGED = "DAMAGED", "Damaged"

    asset_code = models.CharField(max_length=30, unique=True)
    item = models.ForeignKey(
        CatalogItem, on_delete=models.PROTECT, related_name="units"
    )
    condition = models.CharField(
        max_length=10, choices=Condition.choices, default=Condition.GOOD
    )
    acquisition_date = models.DateField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.AVAILABLE
    )

    class Meta:
        ordering = ["asset_code"]

    def __str__(self):
        return f"{self.asset_code} ({self.item.name})"


class MaintenanceRecord(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    unit = models.ForeignKey(
        EquipmentUnit, on_delete=models.PROTECT, related_name="maintenance_records"
    )
    problem = models.TextField()
    action = models.TextField(blank=True)
    opened_at = models.DateField(default=timezone.localdate)
    closed_at = models.DateField(null=True, blank=True)
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="maintenance_handled",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN
    )

    def __str__(self):
        return f"{self.unit.asset_code} - {self.status}"