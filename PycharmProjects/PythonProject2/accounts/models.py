from django.conf import settings
from django.db import models


class Profile(models.Model):
    class Role(models.TextChoices):
        BASIC_USER = "BASIC_USER", "Basic user"
        ADMIN = "ADMIN", "Admin"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(
        max_length=12, choices=Role.choices, default=Role.BASIC_USER
    )
    contact_number = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"