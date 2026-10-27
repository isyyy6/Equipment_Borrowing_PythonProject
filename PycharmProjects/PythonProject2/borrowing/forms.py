from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from inventory.models import CatalogItem

from .models import BorrowRequest, RequestItem, SystemSetting


class BorrowRequestForm(forms.ModelForm):
    class Meta:
        model = BorrowRequest
        fields = ("purpose", "due_date")
        widgets = {
            "purpose": forms.Textarea(attrs={"rows": 3}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {"due_date": "Desired return date"}

    def clean_due_date(self):
        due = self.cleaned_data["due_date"]
        today = timezone.localdate()
        limit = SystemSetting.get().max_loan_days
        if due <= today:
            raise forms.ValidationError("The return date must be after today.")
        if (due - today).days > limit:
            raise forms.ValidationError(
                f"The return date can be at most {limit} days from today."
            )
        return due


class RequestItemForm(forms.ModelForm):
    class Meta:
        model = RequestItem
        fields = ("item", "quantity")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = CatalogItem.objects.filter(
            is_active=True, category__is_active=True
        ).select_related("category")
        self.fields["item"].empty_label = "-- choose an item --"
        limit = SystemSetting.get().max_quantity_per_item
        self.fields["quantity"].widget.attrs.update({"min": 1, "max": limit})

    def clean_quantity(self):
        qty = self.cleaned_data["quantity"]
        limit = SystemSetting.get().max_quantity_per_item
        if qty < 1:
            raise forms.ValidationError("Quantity must be at least 1.")
        if qty > limit:
            raise forms.ValidationError(f"You can request at most {limit} per item.")
        return qty


class BaseRequestItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            item = form.cleaned_data.get("item")
            if item is None:
                continue
            if item in seen:
                raise forms.ValidationError(
                    "Each item can only appear once in a request."
                )
            seen.add(item)


RequestItemFormSet = inlineformset_factory(
    BorrowRequest,
    RequestItem,
    form=RequestItemForm,
    formset=BaseRequestItemFormSet,
    extra=2,
    min_num=1,
    max_num=3,
    validate_min=True,
    validate_max=True,
    can_delete=False,
)