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


@celery_app.task(name="send_notification", bind=True, max_retries=2)
def send_notification(self, user_id: str, title: str, message: str, data: dict | None = None):
    """Look up user/driver FCM token and deliver via Firebase Admin SDK."""
    from app.core.firebase import initialize_firebase
    from app.services import firebase_notification_service as fcm

    initialize_firebase()

    token = None
    role = "user"
    try:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session

        from app.core.config import settings as app_settings
        from app.drivers.models import Driver
        from app.users.models import User

        from uuid import UUID

        entity_id = UUID(str(user_id))
        engine = create_engine(app_settings.database_sync_url)
        with Session(engine) as session:
            user = session.execute(select(User).where(User.id == entity_id)).scalar_one_or_none()
            if user and user.fcm_token:
                token = user.fcm_token
            else:
                driver = session.execute(select(Driver).where(Driver.id == entity_id)).scalar_one_or_none()
                if driver and driver.fcm_token:
                    token = driver.fcm_token
                    role = "driver"

            if not token:
                return {"user_id": user_id, "sent": False, "error": "no_token"}

            result = fcm.send_to_token(token, title, message, data)
            if result.get("invalid_token"):
                if role == "driver":
                    driver = session.execute(select(Driver).where(Driver.id == entity_id)).scalar_one_or_none()
                    if driver:
                        driver.fcm_token = None
                else:
                    user = session.execute(select(User).where(User.id == entity_id)).scalar_one_or_none()
                    if user:
                        user.fcm_token = None
                session.commit()

            return {"user_id": user_id, "role": role, **result}
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        except Exception:
            return {"user_id": user_id, "sent": False, "error": str(exc)}


@celery_app.task(name="process_ride_matching")
def process_ride_matching(ride_id: str):
    return {"ride_id": ride_id, "status": "processed"}


@celery_app.task(name="generate_invoice")
def generate_invoice(payment_id: str):
    return {"payment_id": payment_id, "invoice_url": f"/invoices/{payment_id}.pdf"}
