from celery import Celery

from app.core.settings import settings

celery_app = Celery(
    "virtus",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)
