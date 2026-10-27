from django.contrib import messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.response import TemplateResponse
from django.urls import reverse

from .services import WorkflowError


def confirm_text_action(
    modeladmin, request, queryset, *, action_name, title, runner, field_label, success
):
    """Confirmation page with a required text box.
    runner(obj, text) runs for each selected record and raises WorkflowError to refuse."""
    if "apply" in request.POST:
        text = request.POST.get("text", "").strip()
        if not text:
            messages.error(request, f"{field_label} is required.")
        else:
            done = 0
            for obj in queryset:
                try:
                    runner(obj, text)
                    done += 1
                except WorkflowError as exc:
                    modeladmin.message_user(
                        request, f"{obj}: {exc}", level=messages.ERROR
                    )
            if done:
                modeladmin.message_user(
                    request, f"{done} record(s) {success}.", level=messages.SUCCESS
                )
            return None  # back to the list

    opts = modeladmin.model._meta
    context = {
        **modeladmin.admin_site.each_context(request),
        "title": title,
        "queryset": queryset,
        "action_name": action_name,
        "field_label": field_label,
        "cancel_url": reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"),
        "action_checkbox_name": ACTION_CHECKBOX_NAME,
        "opts": opts,
    }
    return TemplateResponse(request, "borrowing_admin/text_confirm.html", context)