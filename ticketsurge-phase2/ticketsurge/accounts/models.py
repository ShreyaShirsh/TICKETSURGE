from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model.

    We subclass AbstractUser (rather than using django.contrib.auth.User) from
    the very first migration. Django strongly recommends this because switching
    the user model after tables exist is a costly, error-prone migration. Even
    with no extra fields today, having our own model means we can add booking
    history, phone numbers, KYC flags, etc. later without pain.
    """

    # Room to grow in later phases (e.g. rate-limit / scalper heuristics).
    phone = models.CharField(max_length=32, blank=True)

    def __str__(self) -> str:
        return self.get_username()
