from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from inventory.models import CatalogItem, EquipmentUnit


class SystemSetting(models.Model):
    """Single row holding the admin-configurable limits."""
    max_loan_days = models.PositiveIntegerField(
        default=7, validators=[MinValueValidator(1)]
    )
    max_quantity_per_item = models.PositiveIntegerField(
        default=3, validators=[MinValueValidator(1)]
    )

    def __str__(self):
        return "System settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # always the same row
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class BorrowRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"
        ISSUED = "ISSUED", "Issued"
        PARTIALLY_RETURNED = "PARTIALLY_RETURNED", "Partially returned"
        RETURNED = "RETURNED", "Returned"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="borrow_requests",
    )
    purpose = models.TextField()
    requested_at = models.DateTimeField(auto_now_add=True)
    due_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    # recorded decision (A3)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decisions_made",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]

    @property
    def is_overdue(self):
        return self.overdue_days > 0

    @property
    def overdue_days(self):
        """Zero before or on the due date; otherwise calendar days late.
        Only applies while units are still outstanding."""
        if self.status not in (
            self.Status.ISSUED,
            self.Status.PARTIALLY_RETURNED,
        ):
            return 0
        return max((timezone.localdate() - self.due_date).days, 0)

    def __str__(self):
        return f"Request #{self.pk} - {self.user.username} ({self.status})"


class RequestItem(models.Model):
    borrow_request = models.ForeignKey(
        BorrowRequest, on_delete=models.CASCADE, related_name="items"
    )
    item = models.ForeignKey(
        CatalogItem, on_delete=models.PROTECT, related_name="request_items"
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["borrow_request", "item"], name="unique_item_per_request"
            )
        ]

    def __str__(self):
        return f"{self.quantity} x {self.item.name}"


class UnitAllocation(models.Model):
    request_item = models.ForeignKey(
        RequestItem, on_delete=models.PROTECT, related_name="allocations"
    )
    unit = models.ForeignKey(
        EquipmentUnit, on_delete=models.PROTECT, related_name="allocations"
    )
    allocated_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # a unit can have only ONE active allocation at a time
            models.UniqueConstraint(
                fields=["unit"],
                condition=Q(cancelled_at__isnull=True, returned_at__isnull=True),
                name="one_active_allocation_per_unit",
            )
        ]

    @property
    def is_active(self):
        return self.cancelled_at is None and self.returned_at is None

    def __str__(self):
        return f"{self.unit.asset_code} -> request #{self.request_item.borrow_request_id}"


class Loan(models.Model):
    """Created when an approved request is released (one loan per request)."""
    borrow_request = models.OneToOneField(
        BorrowRequest, on_delete=models.PROTECT, related_name="loan"
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="loans_issued",
    )
    issued_at = models.DateTimeField(default=timezone.now)
    due_date = models.DateField()

    def __str__(self):
        return f"Loan for request #{self.borrow_request_id}"


class ReturnRecord(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="returns")
    unit = models.ForeignKey(
        EquipmentUnit, on_delete=models.PROTECT, related_name="return_records"
    )
    returned_at = models.DateTimeField(default=timezone.now)
    condition = models.CharField(
        max_length=10,
        choices=EquipmentUnit.Condition.choices,
        default=EquipmentUnit.Condition.GOOD,
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="returns_received",
    )

    class Meta:
        constraints = [
            # prevents recording the same unit's return twice for one loan
            models.UniqueConstraint(
                fields=["loan", "unit"], name="unique_return_per_unit_per_loan"
            )
        ]

    def __str__(self):
        return f"{self.unit.asset_code} returned ({self.condition})"


class AuditLog(models.Model):
    class Action(models.TextChoices):
        APPROVE = "APPROVE", "Approved request"
        REJECT = "REJECT", "Rejected request"
        CANCEL = "CANCEL", "Cancelled request"
        RELEASE = "RELEASE", "Released equipment"
        RETURN = "RETURN", "Recorded return"
        DUE_DATE_REVISED = "DUE_DATE_REVISED", "Revised return date"
        MAINTENANCE_OPEN = "MAINTENANCE_OPEN", "Opened maintenance"
        MAINTENANCE_CLOSE = "MAINTENANCE_CLOSE", "Closed maintenance"
        ACCOUNT_CHANGE = "ACCOUNT_CHANGE", "Account change"
        CATALOG_CHANGE = "CATALOG_CHANGE", "Catalog change"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    target_type = models.CharField(max_length=50)
    target_id = models.PositiveIntegerField(null=True, blank=True)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.actor} {self.action}"