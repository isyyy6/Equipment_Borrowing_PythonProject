from django.contrib import admin

from borrowing.admin_utils import confirm_text_action
from borrowing.services import close_maintenance, open_maintenance

from .models import Category, CatalogItem, EquipmentUnit, MaintenanceRecord


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(CatalogItem)
class CatalogItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "description")


@admin.action(description="Send selected units to maintenance")
def send_to_maintenance(modeladmin, request, queryset):
    return confirm_text_action(
        modeladmin,
        request,
        queryset,
        action_name="send_to_maintenance",
        title="Send units to maintenance",
        runner=lambda obj, text: open_maintenance(obj, text, request.user),
        field_label="Problem",
        success="sent to maintenance",
    )


@admin.register(EquipmentUnit)
class EquipmentUnitAdmin(admin.ModelAdmin):
    list_display = ("asset_code", "item", "condition", "status", "acquisition_date")
    list_filter = ("status", "condition", "item__category", "item")
    search_fields = ("asset_code", "item__name")
    actions = [send_to_maintenance]


@admin.action(description="Close: repaired, return unit to service")
def close_repaired(modeladmin, request, queryset):
    return confirm_text_action(
        modeladmin,
        request,
        queryset,
        action_name="close_repaired",
        title="Close maintenance: repaired",
        runner=lambda obj, text: close_maintenance(
            obj, request.user, text, retire=False
        ),
        field_label="Action taken",
        success="closed and returned to service",
    )


@admin.action(description="Close: retire unit")
def close_retire(modeladmin, request, queryset):
    return confirm_text_action(
        modeladmin,
        request,
        queryset,
        action_name="close_retire",
        title="Close maintenance: retire unit",
        runner=lambda obj, text: close_maintenance(
            obj, request.user, text, retire=True
        ),
        field_label="Action taken",
        success="closed and the unit retired",
    )


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "status",
        "problem",
        "action",
        "opened_at",
        "closed_at",
        "admin",
    )
    list_filter = ("status",)
    search_fields = ("unit__asset_code", "problem")
    actions = [close_repaired, close_retire]

    # records are opened and closed only through the rules above
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False