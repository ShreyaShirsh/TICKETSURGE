import pytest
from django.utils import timezone

from core.holds import get_redis
from core.models import Customer, Event, Seat


@pytest.fixture(autouse=True)
def _eager_celery(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture(autouse=True)
def _clean_redis_holds():
    """Seat PKs are reused across tests (each test rolls back the DB
    transaction), but Redis isn't transactional — flush the holds DB so
    a leftover hold from a previous test can't collide with this one."""
    r = get_redis()
    r.flushdb()
    yield
    r.flushdb()


@pytest.fixture
def event(db):
    return Event.objects.create(name="Test Show", venue="Test Venue", starts_at=timezone.now())


@pytest.fixture
def seat(event):
    return Seat.objects.create(event=event, section="A", row="1", number=1, available=1)


@pytest.fixture
def customer(db):
    return Customer.objects.create(email="fan@example.com", display_name="Fan")
