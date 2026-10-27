from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from borrowing.views import loan_slip

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="accounts:role_redirect"), name="home"),
    path("admin/", admin.site.urls),
    path("manage/loans/<int:pk>/slip/", loan_slip, name="loan_slip"),
    path("accounts/", include("accounts.urls")),
    path("portal/", include("borrowing.urls")),
]