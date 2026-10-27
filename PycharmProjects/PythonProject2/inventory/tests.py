from datetime import date

from django.test import TestCase

from .models import CatalogItem, Category, EquipmentUnit


class CatalogAvailabilityTests(TestCase):
    def test_serviceable_and_available_counts_exclude_unavailable_states(self):
        category = Category.objects.create(name="Computing")
        item = CatalogItem.objects.create(category=category, name="Laptop")
        for code, status in (
            ("LAP-001", EquipmentUnit.Status.AVAILABLE),
            ("LAP-002", EquipmentUnit.Status.MAINTENANCE),
            ("LAP-003", EquipmentUnit.Status.RETIRED),
        ):
            EquipmentUnit.objects.create(
                asset_code=code,
                item=item,
                acquisition_date=date(2025, 1, 1),
                status=status,
            )

        self.assertEqual(item.serviceable_count, 1)
        self.assertEqual(item.available_count, 1)

# Create your tests here.
