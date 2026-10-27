from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from inventory.models import CatalogItem, Category, EquipmentUnit

from .forms import BorrowRequestForm, RequestItemFormSet
from .models import BorrowRequest, Loan, SystemSetting, UnitAllocation
from .services import WorkflowError, cancel_request


@login_required
def home(request):
    return render(request, "portal/home.html")


@login_required
def browse(request):
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    if not category.isdigit():
        category = ""

    not_serviceable = [
        EquipmentUnit.Status.MAINTENANCE,
        EquipmentUnit.Status.RETIRED,
    ]
    items = (
        CatalogItem.objects.filter(is_active=True, category__is_active=True)
        .select_related("category")
        .annotate(
            serviceable=Count("units", filter=~Q(units__status__in=not_serviceable)),
            available=Count(
                "units", filter=Q(units__status=EquipmentUnit.Status.AVAILABLE)
            ),
        )
    )
    if q:
        items = items.filter(Q(name__icontains=q) | Q(category__name__icontains=q))
    if category:
        items = items.filter(category_id=category)

    paginator = Paginator(items.order_by("name"), 6)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "portal/browse.html",
        {
            "page_obj": page_obj,
            "q": q,
            "category": category,
            "categories": Category.objects.filter(is_active=True).order_by("name"),
        },
    )


@login_required
def request_create(request):
    settings_obj = SystemSetting.get()
    if request.method == "POST":
        form = BorrowRequestForm(request.POST)
        formset = RequestItemFormSet(request.POST, instance=BorrowRequest())
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                borrow = form.save(commit=False)
                borrow.user = request.user
                borrow.status = BorrowRequest.Status.PENDING
                borrow.save()
                formset.instance = borrow
                formset.save()
            messages.success(
                request, "Your request was submitted and is now pending review."
            )
            return redirect("portal:request_detail", pk=borrow.pk)
    else:
        form = BorrowRequestForm()
        formset = RequestItemFormSet(instance=BorrowRequest())
    return render(
        request,
        "portal/request_form.html",
        {
            "form": form,
            "formset": formset,
            "max_days": settings_obj.max_loan_days,
            "max_qty": settings_obj.max_quantity_per_item,
        },
    )


@login_required
def request_list(request):
    status = request.GET.get("status", "")
    requests_qs = (
        BorrowRequest.objects.filter(user=request.user)  # only MY requests
        .prefetch_related("items__item", "items__allocations__unit")
        .order_by("-requested_at")
    )
    if status in BorrowRequest.Status.values:
        requests_qs = requests_qs.filter(status=status)

    paginator = Paginator(requests_qs, 8)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "portal/request_list.html",
        {
            "page_obj": page_obj,
            "status": status,
            "statuses": BorrowRequest.Status.choices,
        },
    )


@login_required
def request_detail(request, pk):
    borrow = get_object_or_404(
        BorrowRequest.objects.prefetch_related(
            "items__item", "items__allocations__unit"
        ),
        pk=pk,
        user=request.user,  # someone else's request gives a 404
    )
    can_cancel = borrow.status in (
        BorrowRequest.Status.PENDING,
        BorrowRequest.Status.APPROVED,
    )
    return render(
        request,
        "portal/request_detail.html",
        {"req": borrow, "can_cancel": can_cancel},
    )


@login_required
@require_POST
def request_cancel(request, pk):
    borrow = get_object_or_404(BorrowRequest, pk=pk, user=request.user)
    try:
        cancel_request(borrow, request.user)
        messages.success(request, f"Request #{borrow.pk} was cancelled.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("portal:request_detail", pk=borrow.pk)


@staff_member_required
def loan_slip(request, pk):
    loan = get_object_or_404(
        Loan.objects.select_related(
            "borrow_request__user__profile", "issued_by"
        ),
        pk=pk,
    )
    units = (
        UnitAllocation.objects.filter(
            request_item__borrow_request=loan.borrow_request,
            released_at__isnull=False,
        )
        .select_related("unit", "request_item__item")
        .order_by("request_item__item__name", "unit__asset_code")
    )
    return render(
        request,
        "borrowing_admin/loan_slip.html",
        {"loan": loan, "borrower": loan.borrow_request.user, "units": units},
    )
