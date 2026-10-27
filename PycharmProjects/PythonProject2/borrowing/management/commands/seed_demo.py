import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from accounts.models import Profile
from borrowing.models import (
    AuditLog,
    BorrowRequest,
    Loan,
    RequestItem,
    ReturnRecord,
    SystemSetting,
    UnitAllocation,
)
from inventory.models import CatalogItem, Category, EquipmentUnit, MaintenanceRecord

User = get_user_model()
Status = BorrowRequest.Status
Action = AuditLog.Action
Condition = EquipmentUnit.Condition

# category -> [(item name, description, asset prefix, number of units)]
CATALOG = {
    "Audio-Visual": [
        ("Projector", "HDMI classroom projector", "PROJ", 3),
        ("Portable Speaker", "Bluetooth speaker with microphone input", "SPK", 3),
    ],
    "Computing": [
        ("Laptop", "Standard laptop for class activities", "LAP", 3),
        ("Tablet", "10-inch tablet with stylus", "TAB", 3),
    ],
    "Laboratory": [
        ("Digital Multimeter", "Handheld multimeter for circuit labs", "DMM", 3),
        ("Soldering Station", "Temperature-controlled soldering station", "SOL", 3),
    ],
}
FIRST_NAMES = ["Miguel", "Daniel", "Alex", "Jerald", "Ivan", "Rick", "Lance", "Kevin"]
RETIRED_CODES = {"TAB-003"}
CONDITION_OVERRIDES = {
    "SOL-001": Condition.DAMAGED,
    "SPK-003": Condition.DAMAGED,
    "TAB-003": Condition.DAMAGED,
    "DMM-001": Condition.FAIR,
}


class Command(BaseCommand):
    help = "Load fictional demonstration data (use --reset to replace existing data)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete catalog, request and basic-user data first (admin accounts are kept).",
        )
        parser.add_argument(
            "--password",
            help="Password for the demo accounts (random if omitted).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            self.wipe()
        elif (
            CatalogItem.objects.exists()
            or BorrowRequest.objects.exists()
            or User.objects.filter(is_staff=False).exists()
        ):
            raise CommandError(
                "Data already exists. Run again with --reset to replace it "
                "(admin accounts are kept)."
            )

        self.now = timezone.now()
        self.today = timezone.localdate()
        password = options["password"] or secrets.token_urlsafe(9)
        SystemSetting.get()

        admin, admin_created = self.get_admin(password)
        users = self.create_users(password)
        self.create_catalog()
        self.create_transactions(admin, users)
        self.derive_statuses()
        self.report(admin, admin_created, password)

    # ---------------------------------------------------------------- setup
    def wipe(self):
        for model in (
            AuditLog,
            ReturnRecord,
            UnitAllocation,
            Loan,
            RequestItem,
            BorrowRequest,
            MaintenanceRecord,
            EquipmentUnit,
            CatalogItem,
            Category,
        ):
            model.objects.all().delete()
        User.objects.filter(is_staff=False).delete()

    def get_admin(self, password):
        admin = (
            User.objects.filter(is_superuser=True, is_active=True)
            .order_by("pk")
            .first()
        )
        created = False
        if admin is None:
            admin = User.objects.create_superuser(
                username="demo_admin", email="demo_admin@example.com", password=password
            )
            created = True
        Profile.objects.get_or_create(user=admin, defaults={"role": Profile.Role.ADMIN})
        return admin, created

    def create_users(self, password):
        users = []
        for i, first in enumerate(FIRST_NAMES, start=1):
            username = f"{first.lower()}.sample"
            user = User.objects.create_user(
                username=username,
                email=f"{username}@example.com",
                password=password,
                first_name=first,
                last_name="Sample",
            )
            user.profile.contact_number = f"0900-000-{i:04d}"
            user.profile.save()
            users.append(user)
        return users

    def create_catalog(self):
        self.items, self.units = {}, {}
        n = 0
        for category_name, items in CATALOG.items():
            category = Category.objects.create(name=category_name)
            for name, description, prefix, count in items:
                item = CatalogItem.objects.create(
                    category=category, name=name, description=description
                )
                self.items[name] = item
                for i in range(1, count + 1):
                    n += 1
                    code = f"{prefix}-{i:03d}"
                    self.units[code] = EquipmentUnit.objects.create(
                        asset_code=code,
                        item=item,
                        acquisition_date=self.today - timedelta(days=180 + n * 9),
                    )

    # -------------------------------------------------------------- helpers
    def audit(self, actor, action, target, detail, when):
        entry = AuditLog.objects.create(
            actor=actor,
            action=action,
            target_type=target.__class__.__name__,
            target_id=target.pk,
            detail=detail,
        )
        AuditLog.objects.filter(pk=entry.pk).update(timestamp=when)

    def make_request(
        self,
        user,
        lines,
        purpose,
        status,
        requested_at,
        due_date,
        decided_by=None,
        decided_at=None,
        reason="",
    ):
        req = BorrowRequest.objects.create(
            user=user,
            purpose=purpose,
            due_date=due_date,
            status=status,
            decided_by=decided_by,
            decided_at=decided_at,
            decision_reason=reason,
        )
        BorrowRequest.objects.filter(pk=req.pk).update(requested_at=requested_at)
        ritems = [
            RequestItem.objects.create(
                borrow_request=req, item=self.items[name], quantity=qty
            )
            for name, qty in lines
        ]
        return req, ritems

    def build_loan(self, admin, user, spec, issued_ago, due_days, purpose, returns=None):
        """spec: [(item name, [asset codes])]; returns: {code: (days after issue, condition)}"""
        returns = returns or {}
        issued_at = self.now - timedelta(days=issued_ago, hours=1)
        decided_at = issued_at - timedelta(hours=12)
        requested_at = issued_at - timedelta(days=1)
        due_date = timezone.localtime(issued_at).date() + timedelta(days=due_days)
        lines = [(name, len(codes)) for name, codes in spec]

        req, ritems = self.make_request(
            user, lines, purpose, Status.ISSUED, requested_at, due_date,
            admin, decided_at, "Approved for class activity",
        )
        loan = Loan.objects.create(
            borrow_request=req, issued_by=admin, issued_at=issued_at, due_date=due_date
        )
        all_codes = [code for _, codes in spec for code in codes]
        self.audit(admin, Action.APPROVE, req, "Approved for class activity", decided_at)
        self.audit(admin, Action.RELEASE, req, "Units: " + ", ".join(all_codes), issued_at)

        for ritem, (_, codes) in zip(ritems, spec):
            for code in codes:
                unit = self.units[code]
                ret = returns.get(code)
                returned_at = issued_at + timedelta(days=ret[0]) if ret else None
                alloc = UnitAllocation.objects.create(
                    request_item=ritem,
                    unit=unit,
                    released_at=issued_at,
                    returned_at=returned_at,
                )
                UnitAllocation.objects.filter(pk=alloc.pk).update(allocated_at=decided_at)
                if ret:
                    ReturnRecord.objects.create(
                        loan=loan,
                        unit=unit,
                        returned_at=returned_at,
                        condition=ret[1],
                        received_by=admin,
                    )
                    self.audit(
                        admin, Action.RETURN, req, f"{code} returned ({ret[1]})", returned_at
                    )

        if len(returns) == len(all_codes):
            final = Status.RETURNED
        elif returns:
            final = Status.PARTIALLY_RETURNED
        else:
            final = Status.ISSUED
        BorrowRequest.objects.filter(pk=req.pk).update(status=final)
        return req

    def maintenance(self, admin, code, problem, opened_ago, closed_ago=None, action=""):
        unit = self.units[code]
        closed = closed_ago is not None
        MaintenanceRecord.objects.create(
            unit=unit,
            problem=problem,
            action=action,
            opened_at=self.today - timedelta(days=opened_ago),
            closed_at=self.today - timedelta(days=closed_ago) if closed else None,
            admin=admin,
            status=MaintenanceRecord.Status.CLOSED if closed else MaintenanceRecord.Status.OPEN,
        )
        self.audit(
            admin, Action.MAINTENANCE_OPEN, unit, problem,
            self.now - timedelta(days=opened_ago),
        )
        if closed:
            self.audit(
                admin, Action.MAINTENANCE_CLOSE, unit, action,
                self.now - timedelta(days=closed_ago),
            )

    # --------------------------------------------------------- transactions
    def create_transactions(self, admin, users):
        good = Condition.GOOD

        # 8 issued loans (3 fully returned, 1 partial, 4 still out; 2 are overdue)
        self.build_loan(
            admin, users[0], [("Projector", ["PROJ-001"])], 75, 5,
            "Thesis defense rehearsal", {"PROJ-001": (4, good)},
        )
        self.build_loan(
            admin, users[1], [("Laptop", ["LAP-001", "LAP-002"])], 58, 6,
            "Programming workshop", {"LAP-001": (6, good), "LAP-002": (6, good)},
        )
        l4 = self.build_loan(
            admin, users[2], [("Soldering Station", ["SOL-001"])], 41, 7,
            "Electronics lab project", {"SOL-001": (7, Condition.DAMAGED)},
        )
        self.maintenance(
            admin, "SOL-001", f"Returned damaged from request #{l4.pk}", opened_ago=34
        )
        self.build_loan(  # partial return, overdue
            admin, users[3], [("Digital Multimeter", ["DMM-001", "DMM-002"])], 27, 7,
            "Circuit analysis activity", {"DMM-001": (5, Condition.FAIR)},
        )
        self.build_loan(  # all units out, overdue
            admin, users[4], [("Portable Speaker", ["SPK-001", "SPK-002"])], 17, 5,
            "Orientation program",
        )
        self.build_loan(
            admin, users[5], [("Tablet", ["TAB-001", "TAB-002"])], 2, 6,
            "Field survey data capture",
        )
        self.build_loan(
            admin, users[6], [("Laptop", ["LAP-001"])], 1, 3, "Thesis consultation",
        )
        self.build_loan(
            admin, users[7], [("Projector", ["PROJ-002"])], 0, 5, "Club presentation",
        )

        # 4 maintenance records in total (one more was opened above)
        self.maintenance(
            admin, "SPK-003", "Speaker cone cracked; no sound on left channel", opened_ago=9
        )
        self.maintenance(
            admin, "LAP-003", "Battery drains quickly", opened_ago=50, closed_ago=44,
            action="Replaced battery and tested OK",
        )
        self.maintenance(
            admin, "TAB-003", "Screen cracked and unresponsive", opened_ago=30, closed_ago=25,
            action="Screen unrepairable; unit retired",
        )

        # 5 pending requests
        pending = [
            (0, [("Projector", 1)], "Group presentation", 5, 4),
            (2, [("Laptop", 2), ("Portable Speaker", 1)], "Department orientation", 20, 5),
            (3, [("Tablet", 1)], "Field data capture", 30, 3),
            (5, [("Digital Multimeter", 2), ("Soldering Station", 1)], "Circuits lab project", 3, 6),
            (7, [("Laptop", 1)], "Thesis consultation", 50, 2),
        ]
        for idx, lines, purpose, hours_ago, due_in in pending:
            self.make_request(
                users[idx], lines, purpose, Status.PENDING,
                self.now - timedelta(hours=hours_ago), self.today + timedelta(days=due_in),
            )

        # 1 approved request that reserves PROJ-003
        decided = self.now - timedelta(hours=18)
        req, ritems = self.make_request(
            users[1], [("Projector", 1)], "Seminar on Friday", Status.APPROVED,
            self.now - timedelta(hours=20), self.today + timedelta(days=4),
            admin, decided, "Units available for that date",
        )
        alloc = UnitAllocation.objects.create(request_item=ritems[0], unit=self.units["PROJ-003"])
        UnitAllocation.objects.filter(pk=alloc.pk).update(allocated_at=decided)
        self.audit(admin, Action.APPROVE, req, "Units available for that date", decided)

        # 3 rejected requests
        rejected = [
            (2, [("Laptop", 3)], "Online exam practice", 12, 4,
             "Requested units were already committed for those dates."),
            (4, [("Portable Speaker", 2)], "Personal event", 9, 3,
             "Purpose is not related to coursework."),
            (6, [("Tablet", 3), ("Projector", 2)], "Class demo", 15, 5,
             "Quantity exceeds what the activity requires; please resubmit with fewer units."),
        ]
        for idx, lines, purpose, days_ago, due_offset, reason in rejected:
            requested_at = self.now - timedelta(days=days_ago)
            decided_at = requested_at + timedelta(hours=6)
            req, _ = self.make_request(
                users[idx], lines, purpose, Status.REJECTED, requested_at,
                timezone.localtime(requested_at).date() + timedelta(days=due_offset),
                admin, decided_at, reason,
            )
            self.audit(admin, Action.REJECT, req, reason, decided_at)

        # 3 cancelled requests (two while Pending, one while Approved)
        for idx, lines, purpose, days_ago in [
            (1, [("Laptop", 1)], "Group study", 8),
            (3, [("Projector", 1)], "Club meeting", 5),
        ]:
            requested_at = self.now - timedelta(days=days_ago)
            req, _ = self.make_request(
                users[idx], lines, purpose, Status.CANCELLED, requested_at,
                timezone.localtime(requested_at).date() + timedelta(days=4),
            )
            self.audit(
                users[idx], Action.CANCEL, req, "Cancelled while Pending",
                requested_at + timedelta(hours=3),
            )

        requested_at = self.now - timedelta(days=11)
        decided_at = requested_at + timedelta(hours=5)
        cancelled_at = requested_at + timedelta(days=1)
        req, ritems = self.make_request(
            users[5], [("Laptop", 1)], "Workshop", Status.CANCELLED, requested_at,
            timezone.localtime(requested_at).date() + timedelta(days=5),
            admin, decided_at, "Approved",
        )
        alloc = UnitAllocation.objects.create(
            request_item=ritems[0], unit=self.units["LAP-002"], cancelled_at=cancelled_at
        )
        UnitAllocation.objects.filter(pk=alloc.pk).update(allocated_at=decided_at)
        self.audit(admin, Action.APPROVE, req, "Approved", decided_at)
        self.audit(users[5], Action.CANCEL, req, "Cancelled while Approved", cancelled_at)

    def derive_statuses(self):
        """Set every unit's status and condition from the records created above."""
        active = UnitAllocation.objects.filter(
            cancelled_at__isnull=True, returned_at__isnull=True
        )
        open_maintenance = set(
            MaintenanceRecord.objects.filter(
                status=MaintenanceRecord.Status.OPEN
            ).values_list("unit_id", flat=True)
        )
        for code, unit in self.units.items():
            current = active.filter(unit=unit).first()
            if current and current.released_at:
                status = EquipmentUnit.Status.ISSUED
            elif current:
                status = EquipmentUnit.Status.RESERVED
            elif unit.pk in open_maintenance:
                status = EquipmentUnit.Status.MAINTENANCE
            elif code in RETIRED_CODES:
                status = EquipmentUnit.Status.RETIRED
            else:
                status = EquipmentUnit.Status.AVAILABLE
            unit.status = status
            unit.condition = CONDITION_OVERRIDES.get(code, Condition.GOOD)
            unit.save(update_fields=["status", "condition"])

    # --------------------------------------------------------------- report
    def report(self, admin, admin_created, password):
        out = self.stdout.write
        out(self.style.SUCCESS("Seed complete."))
        out("")
        out("Requests by status:")
        for row in BorrowRequest.objects.values("status").annotate(n=Count("id")).order_by("status"):
            out(f"  {row['status']:<20} {row['n']}")
        out("Units by status:")
        for row in EquipmentUnit.objects.values("status").annotate(n=Count("id")).order_by("status"):
            out(f"  {row['status']:<20} {row['n']}")
        out("")
        if admin_created:
            out(f"Admin account created: {admin.username}")
        else:
            out(f"Existing admin account kept: {admin.username} (its password is unchanged)")
        out("Basic users: " + ", ".join(f"{n.lower()}.sample" for n in FIRST_NAMES))
        out(f"Password for the demo accounts: {password}")
        out("Do not commit this password; share it only through your instructor's channel.")