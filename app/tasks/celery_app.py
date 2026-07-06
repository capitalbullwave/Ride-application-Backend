from celery import Celery

from app.config.settings import settings

celery_app = Celery(
    "ridebooking",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
)


@celery_app.task(name="send_notification")
def send_notification(user_id: str, title: str, message: str, data: dict | None = None):
    return {"user_id": user_id, "title": title, "message": message, "sent": True}


@celery_app.task(name="process_ride_matching")
def process_ride_matching(ride_id: str):
    return {"ride_id": ride_id, "status": "processed"}


@celery_app.task(name="generate_invoice")
def generate_invoice(payment_id: str):
    return {"payment_id": payment_id, "invoice_url": f"/invoices/{payment_id}.pdf"}
