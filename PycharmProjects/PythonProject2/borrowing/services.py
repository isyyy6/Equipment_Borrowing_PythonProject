from django.db import IntegrityError, transaction
from django.utils import timezone

from inventory.models import EquipmentUnit, MaintenanceRecord

from .audit import log_action

from .models import (
    AuditLog,
    BorrowRequest,
    Loan,
    ReturnRecord,
    SystemSetting,
    UnitAllocation,
)


class WorkflowError(Exception):
    """Raised when an action isn't allowed for the request's current state."""


def approve_request(borrow_request, admin_user, reason=""):
    """Reserve every requested unit, or approve nothing."""
    try:
        with transaction.atomic():
            req = BorrowRequest.objects.select_for_update().get(pk=borrow_request.pk)

            if req.status != BorrowRequest.Status.PENDING:
                raise WorkflowError("Only Pending requests can be approved.")
            if not req.user.is_active:
                raise WorkflowError("The borrower's account is deactivated.")
            if req.due_date < timezone.localdate():
                raise WorkflowError("The requested return date has already passed.")

            for ri in req.items.select_related("item"):
                units = list(
                    EquipmentUnit.objects.select_for_update()
                    .filter(item=ri.item, status=EquipmentUnit.Status.AVAILABLE)
                    .order_by("asset_code")[: ri.quantity]
                )
                if len(units) < ri.quantity:
                    raise WorkflowError(
                        f"Not enough available units of {ri.item.name}: "
                        f"{ri.quantity} requested, {len(units)} available."
                    )
                for unit in units:
                    UnitAllocation.objects.create(request_item=ri, unit=unit)
                    unit.status = EquipmentUnit.Status.RESERVED
                    unit.save(update_fields=["status"])

            req.status = BorrowRequest.Status.APPROVED
            req.decided_by = admin_user
            req.decided_at = timezone.now()
            req.decision_reason = (reason or "").strip()
            req.save(
                update_fields=["status", "decided_by", "decided_at", "decision_reason"]
            )
            log_action(admin_user, AuditLog.Action.APPROVE, req, req.decision_reason)
    except IntegrityError:
        raise WorkflowError(
            "Those units were just reserved by another approval. Please try again."
        )
    return req


def reject_request(borrow_request, admin_user, reason):
    reason = (reason or "").strip()
    if not reason:
        raise WorkflowError("A reason is required to reject a request.")
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().get(pk=borrow_request.pk)
        if req.status != BorrowRequest.Status.PENDING:
            raise WorkflowError("Only Pending requests can be rejected.")
        req.status = BorrowRequest.Status.REJECTED
        req.decided_by = admin_user
        req.decided_at = timezone.now()
        req.decision_reason = reason
        req.save(
            update_fields=["status", "decided_by", "decided_at", "decision_reason"]
        )
        log_action(admin_user, AuditLog.Action.REJECT, req, reason)
    return req


def cancel_request(borrow_request, actor):
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().get(pk=borrow_request.pk)

        if req.status not in (
            BorrowRequest.Status.PENDING,
            BorrowRequest.Status.APPROVED,
        ):
            raise WorkflowError(
                "Only Pending or Approved requests can be cancelled."
            )

        previous = req.get_status_display()
        if req.status == BorrowRequest.Status.APPROVED:
            active = UnitAllocation.objects.filter(
                request_item__borrow_request=req,
                cancelled_at__isnull=True,
                returned_at__isnull=True,
            )
            unit_ids = list(active.values_list("unit_id", flat=True))
            active.update(cancelled_at=timezone.now())
            EquipmentUnit.objects.filter(
                pk__in=unit_ids, status=EquipmentUnit.Status.RESERVED
            ).update(status=EquipmentUnit.Status.AVAILABLE)

        req.status = BorrowRequest.Status.CANCELLED
        req.save(update_fields=["status"])
        log_action(
            actor, AuditLog.Action.CANCEL, req, f"Cancelled while {previous}"
        )
    return req


def release_request(borrow_request, admin_user):
    """Turn an Approved request into an Issued loan."""
    try:
        with transaction.atomic():
            req = BorrowRequest.objects.select_for_update().get(
                pk=borrow_request.pk
            )

            if req.status != BorrowRequest.Status.APPROVED:
                raise WorkflowError(
                    f"Only Approved requests can be released "
                    f"(this one is {req.get_status_display()})."
                )
            if not req.user.is_active:
                raise WorkflowError("The borrower's account is deactivated.")
            if req.due_date < timezone.localdate():
                raise WorkflowError(
                    "The agreed return date has passed. "
                    "Revise the return date before releasing."
                )

            allocations = UnitAllocation.objects.select_for_update().filter(
                request_item__borrow_request=req,
                cancelled_at__isnull=True,
                returned_at__isnull=True,
            )
            for ri in req.items.select_related("item"):
                count = allocations.filter(request_item=ri).count()
                if count != ri.quantity:
                    raise WorkflowError(
                        f"{ri.item.name}: {ri.quantity} requested but only "
                        f"{count} unit(s) allocated."
                    )

            now = timezone.now()
            unit_ids = list(allocations.values_list("unit_id", flat=True))
            allocations.update(released_at=now)
            changed = EquipmentUnit.objects.filter(
                pk__in=unit_ids, status=EquipmentUnit.Status.RESERVED
            ).update(status=EquipmentUnit.Status.ISSUED)
            if changed != len(unit_ids):
                raise WorkflowError(
                    "Some allocated units are no longer in Reserved status."
                )

            Loan.objects.create(
                borrow_request=req,
                issued_by=admin_user,
                issued_at=now,
                due_date=req.due_date,
            )
            req.status = BorrowRequest.Status.ISSUED
            req.save(update_fields=["status"])
            codes = ", ".join(
                EquipmentUnit.objects.filter(pk__in=unit_ids)
                .order_by("asset_code")
                .values_list("asset_code", flat=True)
            )
            log_action(admin_user, AuditLog.Action.RELEASE, req, f"Units: {codes}")
    except IntegrityError:
        raise WorkflowError("This request already has an issued loan.")
    return req

def record_returns(borrow_request, returns, admin_user):
    """returns: a list of (unit_id, condition) pairs for units handed back."""
    if not returns:
        raise WorkflowError("Tick at least one unit to record a return.")
    valid_conditions = set(EquipmentUnit.Condition.values)
    try:
        with transaction.atomic():
            req = BorrowRequest.objects.select_for_update().get(
                pk=borrow_request.pk
            )
            if req.status not in (
                BorrowRequest.Status.ISSUED,
                BorrowRequest.Status.PARTIALLY_RETURNED,
            ):
                raise WorkflowError(
                    "Returns can only be recorded for Issued or "
                    "Partially returned requests."
                )
            try:
                loan = req.loan
            except Loan.DoesNotExist:
                raise WorkflowError("This request has no issued loan.")

            now = timezone.now()
            for unit_id, condition in returns:
                if condition not in valid_conditions:
                    raise WorkflowError("Invalid unit condition.")
                alloc = (
                    UnitAllocation.objects.select_for_update()
                    .select_related("unit")
                    .filter(
                        request_item__borrow_request=req,
                        unit_id=unit_id,
                        released_at__isnull=False,
                        cancelled_at__isnull=True,
                        returned_at__isnull=True,
                    )
                    .first()
                )
                if alloc is None:
                    raise WorkflowError(
                        "A selected unit is not outstanding on this loan "
                        "(it may already be returned)."
                    )

                unit = alloc.unit
                ReturnRecord.objects.create(
                    loan=loan,
                    unit=unit,
                    returned_at=now,
                    condition=condition,
                    received_by=admin_user,
                )
                alloc.returned_at = now
                alloc.save(update_fields=["returned_at"])

                unit.condition = condition
                if condition == EquipmentUnit.Condition.DAMAGED:
                    # damaged units are never made available straight away
                    unit.status = EquipmentUnit.Status.MAINTENANCE
                    MaintenanceRecord.objects.create(
                        unit=unit,
                        problem=f"Returned damaged from request #{req.pk}",
                        admin=admin_user,
                    )
                    log_action(
                        admin_user,
                        AuditLog.Action.MAINTENANCE_OPEN,
                        unit,
                        f"Opened automatically: damaged return from request #{req.pk}",
                    )
                else:
                    unit.status = EquipmentUnit.Status.AVAILABLE
                unit.save(update_fields=["condition", "status"])
                log_action(
                    admin_user,
                    AuditLog.Action.RETURN,
                    req,
                    f"{unit.asset_code} returned ({condition})",
                )

            outstanding = UnitAllocation.objects.filter(
                request_item__borrow_request=req,
                released_at__isnull=False,
                cancelled_at__isnull=True,
                returned_at__isnull=True,
            ).count()
            req.status = (
                BorrowRequest.Status.RETURNED
                if outstanding == 0
                else BorrowRequest.Status.PARTIALLY_RETURNED
            )
            req.save(update_fields=["status"])
    except IntegrityError:
        raise WorkflowError("A return was already recorded for one of these units.")
    return req

def open_maintenance(unit, problem, admin_user):
    problem = (problem or "").strip()
    if not problem:
        raise WorkflowError("A problem description is required.")
    with transaction.atomic():
        unit = EquipmentUnit.objects.select_for_update().get(pk=unit.pk)
        if unit.status in (
            EquipmentUnit.Status.ISSUED,
            EquipmentUnit.Status.RESERVED,
        ):
            raise WorkflowError(
                f"{unit.asset_code} is currently {unit.get_status_display()} "
                f"and cannot be sent to maintenance."
            )
        if unit.status != EquipmentUnit.Status.AVAILABLE:
            raise WorkflowError(
                f"{unit.asset_code} is already {unit.get_status_display()}."
            )
        record = MaintenanceRecord.objects.create(
            unit=unit, problem=problem, admin=admin_user
        )
        unit.status = EquipmentUnit.Status.MAINTENANCE
        unit.save(update_fields=["status"])
        log_action(admin_user, AuditLog.Action.MAINTENANCE_OPEN, unit, problem)
    return record


def close_maintenance(record, admin_user, action_taken, retire=False):
    action_taken = (action_taken or "").strip()
    if not action_taken:
        raise WorkflowError("Describe the action taken.")
    with transaction.atomic():
        rec = MaintenanceRecord.objects.select_for_update().get(pk=record.pk)
        if rec.status != MaintenanceRecord.Status.OPEN:
            raise WorkflowError("This maintenance record is already closed.")
        unit = EquipmentUnit.objects.select_for_update().get(pk=rec.unit_id)
        if unit.status != EquipmentUnit.Status.MAINTENANCE:
            raise WorkflowError(
                f"{unit.asset_code} is not in Maintenance status "
                f"(it is {unit.get_status_display()})."
            )

        rec.status = MaintenanceRecord.Status.CLOSED
        rec.closed_at = timezone.localdate()
        rec.action = action_taken
        rec.save(update_fields=["status", "closed_at", "action"])

        if retire:
            unit.status = EquipmentUnit.Status.RETIRED
            outcome = "Retired"
        else:
            unit.status = EquipmentUnit.Status.AVAILABLE
            unit.condition = EquipmentUnit.Condition.GOOD  # repaired
            outcome = "Returned to service"
        unit.save(update_fields=["status", "condition"])
        log_action(
            admin_user,
            AuditLog.Action.MAINTENANCE_CLOSE,
            unit,
            f"{outcome}: {action_taken}",
        )
    return rec

def revise_due_date(borrow_request, admin_user, new_due, reason):
    """Change the agreed return date before release, with a recorded reason."""
    reason = (reason or "").strip()
    if not reason:
        raise WorkflowError("A reason is required to revise the return date.")
    today = timezone.localdate()
    limit = SystemSetting.get().max_loan_days
    if new_due <= today:
        raise WorkflowError("The new return date must be after today.")
    if (new_due - today).days > limit:
        raise WorkflowError(
            f"The new return date can be at most {limit} days from today."
        )
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().get(pk=borrow_request.pk)
        if req.status not in (
            BorrowRequest.Status.PENDING,
            BorrowRequest.Status.APPROVED,
        ):
            raise WorkflowError(
                "The return date can only be revised before release "
                "(Pending or Approved requests)."
            )
        old = req.due_date
        req.due_date = new_due
        req.save(update_fields=["due_date"])
        log_action(
            admin_user,
            AuditLog.Action.DUE_DATE_REVISED,
            req,
            f"{old:%Y-%m-%d} -> {new_due:%Y-%m-%d}. Reason: {reason}",
        )
    return req