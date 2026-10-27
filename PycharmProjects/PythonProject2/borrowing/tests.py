from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile
from inventory.models import Category, CatalogItem, EquipmentUnit, MaintenanceRecord

from .models import BorrowRequest, Loan, RequestItem, SystemSetting, UnitAllocation
from .services import (
    WorkflowError,
    approve_request,
    cancel_request,
    record_returns,
    release_request,
)

User = get_user_model()


class BorrowingWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.test",
            password="safe-test-password",
        )
        self.user = User.objects.create_user(
            username="borrower", password="safe-test-password"
        )
        self.assertEqual(self.admin.profile.role, Profile.Role.ADMIN)
        self.category = Category.objects.create(name="Classroom")
        self.item = CatalogItem.objects.create(
            category=self.category, name="Projector", description="HDMI projector"
        )
        self.units = [
            EquipmentUnit.objects.create(
                asset_code=f"PROJ-{number:03d}",
                item=self.item,
                acquisition_date=timezone.localdate(),
            )
            for number in (1, 2)
        ]
        self.settings = SystemSetting.get()
        self.settings.max_loan_days = 7
        self.settings.max_quantity_per_item = 3
        self.settings.save()

    def make_request(self, lines, status=BorrowRequest.Status.PENDING):
        req = BorrowRequest.objects.create(
            user=self.user,
            purpose="Class presentation",
            due_date=timezone.localdate() + timedelta(days=3),
            status=status,
        )
        for item, quantity in lines:
            RequestItem.objects.create(
                borrow_request=req, item=item, quantity=quantity
            )
        return req

    def test_approval_reserves_the_requested_unit(self):
        req = self.make_request([(self.item, 1)])

        approved = approve_request(req, self.admin)

        self.assertEqual(approved.status, BorrowRequest.Status.APPROVED)
        self.units[0].refresh_from_db()
        self.assertEqual(self.units[0].status, EquipmentUnit.Status.RESERVED)
        self.assertEqual(
            UnitAllocation.objects.filter(request_item__borrow_request=req).count(), 1
        )

    def test_two_requests_cannot_both_reserve_the_last_unit(self):
        EquipmentUnit.objects.filter(pk=self.units[1].pk).delete()
        first = self.make_request([(self.item, 1)])
        second = self.make_request([(self.item, 1)])

        approve_request(first, self.admin)
        with self.assertRaises(WorkflowError):
            approve_request(second, self.admin)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, BorrowRequest.Status.APPROVED)
        self.assertEqual(second.status, BorrowRequest.Status.PENDING)
        self.assertEqual(
            UnitAllocation.objects.filter(unit=self.units[0]).count(), 1
        )

    def test_insufficient_multi_item_approval_rolls_back_every_reservation(self):
        unavailable_item = CatalogItem.objects.create(
            category=self.category, name="Document camera"
        )
        req = self.make_request([(self.item, 1), (unavailable_item, 1)])

        with self.assertRaises(WorkflowError):
            approve_request(req, self.admin)

        req.refresh_from_db()
        self.assertEqual(req.status, BorrowRequest.Status.PENDING)
        self.assertFalse(UnitAllocation.objects.filter(request_item__borrow_request=req).exists())
        self.assertEqual(
            EquipmentUnit.objects.filter(status=EquipmentUnit.Status.AVAILABLE).count(),
            2,
        )

    def test_cancelling_approved_request_releases_reserved_unit(self):
        req = self.make_request([(self.item, 1)])
        approve_request(req, self.admin)

        cancel_request(req, self.user)

        req.refresh_from_db()
        self.units[0].refresh_from_db()
        allocation = UnitAllocation.objects.get(request_item__borrow_request=req)
        self.assertEqual(req.status, BorrowRequest.Status.CANCELLED)
        self.assertEqual(self.units[0].status, EquipmentUnit.Status.AVAILABLE)
        self.assertIsNotNone(allocation.cancelled_at)

    def test_release_creates_loan_and_issues_reserved_units(self):
        req = self.make_request([(self.item, 1)])
        approve_request(req, self.admin)

        released = release_request(req, self.admin)

        self.units[0].refresh_from_db()
        self.assertEqual(released.status, BorrowRequest.Status.ISSUED)
        self.assertEqual(self.units[0].status, EquipmentUnit.Status.ISSUED)
        self.assertEqual(Loan.objects.get(borrow_request=req).issued_by, self.admin)

    def test_partial_and_damaged_returns_update_request_and_unit_states(self):
        req = self.make_request([(self.item, 2)])
        approve_request(req, self.admin)
        release_request(req, self.admin)

        record_returns(
            req, [(self.units[0].pk, EquipmentUnit.Condition.GOOD)], self.admin
        )
        req.refresh_from_db()
        self.units[0].refresh_from_db()
        self.assertEqual(req.status, BorrowRequest.Status.PARTIALLY_RETURNED)
        self.assertEqual(self.units[0].status, EquipmentUnit.Status.AVAILABLE)

        record_returns(
            req, [(self.units[1].pk, EquipmentUnit.Condition.DAMAGED)], self.admin
        )
        req.refresh_from_db()
        self.units[1].refresh_from_db()
        self.assertEqual(req.status, BorrowRequest.Status.RETURNED)
        self.assertEqual(self.units[1].status, EquipmentUnit.Status.MAINTENANCE)
        self.assertTrue(MaintenanceRecord.objects.filter(unit=self.units[1]).exists())

    def test_user_cannot_view_another_users_request(self):
        other_user = User.objects.create_user(
            username="other", password="safe-test-password"
        )
        req = BorrowRequest.objects.create(
            user=other_user,
            purpose="Other class activity",
            due_date=timezone.localdate() + timedelta(days=2),
        )
        self.client.login(username="borrower", password="safe-test-password")

        response = self.client.get(
            reverse("portal:request_detail", kwargs={"pk": req.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_user_cannot_cancel_another_users_request(self):
        other_user = User.objects.create_user(
            username="other", password="safe-test-password"
        )
        req = BorrowRequest.objects.create(
            user=other_user,
            purpose="Other class activity",
            due_date=timezone.localdate() + timedelta(days=2),
        )
        self.client.login(username="borrower", password="safe-test-password")

        response = self.client.post(
            reverse("portal:request_cancel", kwargs={"pk": req.pk})
        )

        req.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(req.status, BorrowRequest.Status.PENDING)

    def test_history_contains_only_the_signed_in_users_requests(self):
        own_request = self.make_request([(self.item, 1)])
        other_user = User.objects.create_user(
            username="other", password="safe-test-password"
        )
        BorrowRequest.objects.create(
            user=other_user,
            purpose="Other class activity",
            due_date=timezone.localdate() + timedelta(days=2),
        )
        self.client.login(username="borrower", password="safe-test-password")

        response = self.client.get(reverse("portal:request_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["page_obj"].object_list), [own_request]
        )

# Create your tests here.
