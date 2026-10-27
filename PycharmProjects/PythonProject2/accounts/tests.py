from django.contrib.auth import get_user_model
from django.test import TestCase

from .forms import ProfileEditForm, RegisterForm

User = get_user_model()


class AccountFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="borrower", password="safe-test-password"
        )

    def test_registration_does_not_expose_privileged_fields(self):
        form = RegisterForm()

        self.assertNotIn("role", form.fields)
        self.assertNotIn("is_active", form.fields)
        self.assertNotIn("is_staff", form.fields)
        self.assertNotIn("is_superuser", form.fields)

    def test_profile_form_only_edits_personal_details(self):
        form = ProfileEditForm(instance=self.user)

        self.assertEqual(
            set(form.fields), {"first_name", "last_name", "email", "contact_number"}
        )
        self.assertNotIn("role", form.fields)
        self.assertNotIn("is_active", form.fields)

# Create your tests here.
