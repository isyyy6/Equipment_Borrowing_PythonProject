from datetime import date

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html

from inventory.models import EquipmentUnit

from .models import (
    AuditLog,
    BorrowRequest,
    Loan,
    RequestItem,
    ReturnRecord,
    SystemSetting,
    UnitAllocation,
)
from .services import (
    WorkflowError,
    approve_request,
    record_returns,
    reject_request,
    release_request,
    revise_due_date,
)

PAST_TENSE = {"approve": "approved", "reject": "rejected", "release": "released"}


class ReadOnlyAdmin(admin.ModelAdmin):
    """Records created only by workflow actions, never edited by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def _decide(modeladmin, request, queryset, action, require_reason):
    """Shows a confirmation page, then runs the action on each selected request."""
    if "apply" in request.POST:
        reason = request.POST.get("reason", "").strip()
        if require_reason and not reason:
            messages.error(request, "A reason is required to reject requests.")
        else:
            done = 0
            for req in queryset.order_by("requested_at", "pk"):
                try:
                    if action == "approve":
                        approve_request(req, request.user, reason)
                    elif action == "reject":
                        reject_request(req, request.user, reason)
                    else:
                        release_request(req, request.user)
                    done += 1
                except WorkflowError as exc:
                    modeladmin.message_user(
                        request, f"Request #{req.pk}: {exc}", level=messages.ERROR
                    )
            if done:
                modeladmin.message_user(
                    request,
                    f"{done} request(s) {PAST_TENSE[action]}.",
                    level=messages.SUCCESS,
                )
            return None  # back to the list

    context = {
        **modeladmin.admin_site.each_context(request),
        "title": f"{action.capitalize()} requests",
        "queryset": queryset,
        "action": action,
        "require_reason": require_reason,
        "show_reason": action != "release",
        "action_checkbox_name": ACTION_CHECKBOX_NAME,
        "opts": modeladmin.model._meta,
    }
    return TemplateResponse(
        request, "borrowing_admin/decision_confirm.html", context
    )


@admin.action(description="Approve selected requests (reserve units)")
def approve_selected(modeladmin, request, queryset):
    return _decide(modeladmin, request, queryset, "approve", require_reason=False)


@admin.action(description="Reject selected requests")
def reject_selected(modeladmin, request, queryset):
    return _decide(modeladmin, request, queryset, "reject", require_reason=True)


@admin.action(description="Release selected requests (issue loan)")
def release_selected(modeladmin, request, queryset):
    return _decide(modeladmin, request, queryset, "release", require_reason=False)


@admin.action(description="Revise return date (before release)")
def revise_due_date_selected(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Select exactly one request to revise its return date.",
            level=messages.ERROR,
        )
        return None
    req = queryset.get()

    if "apply" in request.POST:
        try:
            new_due = date.fromisoformat(request.POST.get("new_due", ""))
        except ValueError:
            modeladmin.message_user(
                request, "Enter a valid date.", level=messages.ERROR
            )
        else:
            try:
                revise_due_date(
                    req, request.user, new_due, request.POST.get("text", "")
                )
            except WorkflowError as exc:
                modeladmin.message_user(request, str(exc), level=messages.ERROR)
            else:
                modeladmin.message_user(
                    request,
                    f"Return date for request #{req.pk} revised.",
                    level=messages.SUCCESS,
                )
                return None

    context = {
        **modeladmin.admin_site.each_context(request),
        "title": f"Revise return date - request #{req.pk}",
        "req": req,
        "max_days": SystemSetting.get().max_loan_days,
        "action_checkbox_name": ACTION_CHECKBOX_NAME,
        "opts": modeladmin.model._meta,
    }
    return TemplateResponse(
        request, "borrowing_admin/revise_due_date.html", context
    )


@admin.action(description="Record returns for selected request")
def record_returns_selected(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Select exactly one request to record returns.",
            level=messages.ERROR,
        )
        return None
    req = queryset.get()
    outstanding = (
        UnitAllocation.objects.filter(
            request_item__borrow_request=req,
            released_at__isnull=False,
            cancelled_at__isnull=True,
            returned_at__isnull=True,
        )
        .select_related("unit", "request_item__item")
        .order_by("unit__asset_code")
    )
    if not outstanding:
        modeladmin.message_user(
            request,
            f"Request #{req.pk} has no outstanding units to return.",
            level=messages.ERROR,
        )
        return None

    if "apply" in request.POST:
        returns = [
            (alloc.unit_id, request.POST.get(f"condition_{alloc.unit_id}", ""))
            for alloc in outstanding
            if request.POST.get(f"return_{alloc.unit_id}")
        ]
        try:
            record_returns(req, returns, request.user)
        except WorkflowError as exc:
            modeladmin.message_user(request, str(exc), level=messages.ERROR)
        else:
            modeladmin.message_user(
                request,
                f"Returns recorded for request #{req.pk}.",
                level=messages.SUCCESS,
            )
            return None

    context = {
        **modeladmin.admin_site.each_context(request),
        "title": f"Record returns - request #{req.pk}",
        "req": req,
        "outstanding": outstanding,
        "conditions": EquipmentUnit.Condition.choices,
        "action_checkbox_name": ACTION_CHECKBOX_NAME,
        "opts": modeladmin.model._meta,
    }
    return TemplateResponse(
        request, "borrowing_admin/returns_form.html", context
    )


class RequestItemInline(admin.TabularInline):
    model = RequestItem
    extra = 0
    fields = ("item", "quantity", "available_now")
    readonly_fields = ("available_now",)

    @admin.display(description="Available now")
    def available_now(self, obj):
        return obj.item.available_count if obj.pk else "-"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BorrowRequest)
class BorrowRequestAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "user",
        "status",
        "due_date",
        "overdue",
        "requested_at",
        "slip",
    )
    list_filter = ("status", "due_date")
    search_fields = ("user__username", "purpose")
    date_hierarchy = "requested_at"
    list_per_page = 20
    inlines = [RequestItemInline]
    actions = [
        approve_selected,
        reject_selected,
        revise_due_date_selected,
        release_selected,
        record_returns_selected,
    ]

    @admin.display(description="Overdue")
    def overdue(self, obj):
        if obj.is_overdue:
            return format_html(
                '<strong style="color:#b00020;">Overdue: {} day{}</strong>',
                obj.overdue_days,
                "" if obj.overdue_days == 1 else "s",
            )
        return "-"

    @admin.display(description="Slip")
    def slip(self, obj):
        loan = getattr(obj, "loan", None)
        if loan is None:
            return "-"
        return format_html(
            '<a href="{}" target="_blank">Print slip</a>',
            reverse("loan_slip", args=[loan.pk]),
        )


@admin.register(UnitAllocation)
class UnitAllocationAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "request_item",
        "allocated_at",
        "released_at",
        "cancelled_at",
        "returned_at",
    )
    list_filter = ("cancelled_at", "returned_at")


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("max_loan_days", "max_quantity_per_item")

    def has_add_permission(self, request):
        return not SystemSetting.objects.exists()


@admin.register(Loan)
class LoanAdmin(ReadOnlyAdmin):
    list_display = ("borrow_request", "issued_by", "issued_at", "due_date", "slip")
    list_filter = ("due_date",)
    search_fields = ("borrow_request__user__username",)

    @admin.display(description="Slip")
    def slip(self, obj):
        return format_html(
            '<a href="{}" target="_blank">Print slip</a>',
            reverse("loan_slip", args=[obj.pk]),
        )


@admin.register(ReturnRecord)
class ReturnRecordAdmin(ReadOnlyAdmin):
    list_display = ("loan", "unit", "returned_at", "condition", "received_by")
    list_filter = ("condition",)


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("timestamp", "actor", "action", "target_type", "target_id", "detail")
    list_filter = ("action", "target_type")
    search_fields = ("actor__username", "detail", "target_type")
    date_hierarchy = "timestamp"
    list_per_page = 25