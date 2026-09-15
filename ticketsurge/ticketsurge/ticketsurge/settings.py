"""
Django settings for TicketSurge.

Database backend is switchable via the DB_ENGINE env var so the same
codebase runs against Oracle (primary, per the project's DB strategy),
MySQL (fallback) or PostgreSQL (last resort) without code changes -
only connection settings differ. SQLite is supported as a zero-install
option for quick local iteration and CI smoke tests.
"""
import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key-change-in-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "django_celery_results",
    "corsheaders",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = "ticketsurge.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "ticketsurge.wsgi.application"
ASGI_APPLICATION = "ticketsurge.asgi.application"

# --------------------------------------------------------------------------
# Database — Oracle (primary) -> MySQL (fallback) -> PostgreSQL (last
# resort) -> SQLite (dev/CI only, not part of the fallback ladder proper).
# Select with DB_ENGINE=oracle|mysql|postgres|sqlite
# --------------------------------------------------------------------------
DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite")

if DB_ENGINE == "oracle":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.oracle",
            # Easy Connect string: host:port/service_name
            "NAME": os.environ.get("ORACLE_DSN", "localhost:1521/FREEPDB1"),
            "USER": os.environ.get("ORACLE_APP_USER", "ticketsurge"),
            "PASSWORD": os.environ.get("ORACLE_APP_PASSWORD", "ticketsurge"),
            "OPTIONS": {"threaded": True},
        }
    }
elif DB_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("MYSQL_DB", "ticketsurge"),
            "USER": os.environ.get("MYSQL_USER", "ticketsurge"),
            "PASSWORD": os.environ.get("MYSQL_PASSWORD", "ticketsurge"),
            "HOST": os.environ.get("MYSQL_HOST", "localhost"),
            "PORT": os.environ.get("MYSQL_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }
elif DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "ticketsurge"),
            "USER": os.environ.get("POSTGRES_USER", "ticketsurge"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "ticketsurge"),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            # Reuse connections across requests instead of opening a new one
            # per request — under flash-sale concurrency, connection churn
            # against Postgres's connection limit is a real bottleneck (see
            # the load-test notes in README.md). A pgbouncer in front of
            # Postgres is the production-grade fix; this is the cheap one.
            "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", 60)),
        }
    }
else:  # sqlite — dev / CI / this-sandbox default
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Redis — seat holds, DRF throttling cache, and the Channels layer.
# --------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_HOLDS_DB = os.environ.get("REDIS_HOLDS_URL", "redis://localhost:6379/1")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [os.environ.get("REDIS_CHANNELS_URL", REDIS_URL)]},
    }
}

# Seat hold TTL — mirrors the 7-minute checkout window on the frontend.
SEAT_HOLD_TTL_SECONDS = int(os.environ.get("SEAT_HOLD_TTL_SECONDS", 7 * 60))

# --------------------------------------------------------------------------
# Celery / RabbitMQ — async side-effects off the booking request path.
# --------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = "django-db"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"

# In test/dev-without-a-broker mode tasks execute synchronously & eagerly,
# which is what lets the saga/async pipeline be exercised without RabbitMQ.
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "0") == "1"
CELERY_TASK_EAGER_PROPAGATES = True

# Dead-letter routing: poison messages that exhaust retries land in
# ticketsurge.dead_letter instead of vanishing.
CELERY_TASK_ROUTES = {
    "core.tasks.*": {"queue": "ticketsurge"},
}
from kombu import Queue, Exchange  # noqa: E402

dead_letter_exchange = Exchange("dead_letter", type="direct")
main_exchange = Exchange("ticketsurge", type="direct")

CELERY_TASK_QUEUES = (
    Queue(
        "ticketsurge",
        exchange=main_exchange,
        routing_key="ticketsurge",
        queue_arguments={
            "x-dead-letter-exchange": "dead_letter",
            "x-dead-letter-routing-key": "dead_letter",
        },
    ),
    Queue("dead_letter", exchange=dead_letter_exchange, routing_key="dead_letter"),
)
CELERY_TASK_DEFAULT_QUEUE = "ticketsurge"

# --------------------------------------------------------------------------
# DRF
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
