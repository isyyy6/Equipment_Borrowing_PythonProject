from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    contact_number = forms.CharField(max_length=20)

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            # the signal already created the Profile as BASIC_USER
            user.profile.contact_number = self.cleaned_data["contact_number"]
            user.profile.save()
        return user

class ProfileEditForm(forms.ModelForm):
    contact_number = forms.CharField(max_length=20)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact_number"].initial = self.instance.profile.contact_number

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.profile.contact_number = self.cleaned_data["contact_number"]
            user.profile.save()
        return user