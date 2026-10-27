from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import ProfileEditForm, RegisterForm
from .models import Profile


def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:role_redirect")
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("accounts:role_redirect")
    else:
        form = RegisterForm()
    return render(request, "registration/register.html", {"form": form})


@login_required
def role_redirect(request):
    """Admins go to Django Admin, basic users go to the portal."""
    if request.user.profile.role == Profile.Role.ADMIN:
        return redirect("admin:index")
    return redirect("portal:home")


@login_required
def profile_edit(request):
    if request.method == "POST":
        form = ProfileEditForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile was updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileEditForm(instance=request.user)
    return render(request, "accounts/profile_edit.html", {"form": form})