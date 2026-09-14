"""
Django settings for the TicketSurge project.

Configuration is driven by environment variables (see .env.example) so the same
code runs locally, in Docker, and in CI without edits. Secrets never live in
source control.
"""

from pathlib import Path

import os
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a local .env file if present. In production, real environment variables
# take precedence and this is simply a no-op.
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


# --- Core -------------------------------------------------------------------

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    # Local
    "accounts",
    "tickets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---------------------------------------------------------------
# PostgreSQL is the system of record. dj-database-url parses a single
# DATABASE_URL string (postgres://user:pass@host:port/name).

DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv(
            "DATABASE_URL",
            "postgres://ticketsurge:ticketsurge@127.0.0.1:5432/ticketsurge",
        ),
        conn_max_age=600,
    )
}

# --- Auth -------------------------------------------------------------------
# Custom user model set from day one; swapping it later is a painful migration.

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- DRF --------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",  # tightened in a later phase
    ],
}

# --- Redis --------------------------------------------------------------------
# Used for short-lived seat holds (SET seat:{id} ... NX EX). Redis is a fast
# coordination layer and a UX device (the checkout timer); it is NOT the
# correctness guarantee — PostgreSQL row locks are (see tickets/services.py).

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

# How long a seat hold lasts before it auto-expires (seconds). 7 minutes.
SEAT_HOLD_TTL_SECONDS = int(os.getenv("SEAT_HOLD_TTL_SECONDS", "420"))

# --- i18n / static ----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
