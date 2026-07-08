"""Production FCM delivery via Firebase Admin SDK."""
from __future__ import annotations

import time
from typing import Any, Iterable
from uuid import uuid4

from firebase_admin import messaging
from firebase_admin.exceptions import FirebaseError, InvalidArgumentError
from firebase_admin.messaging import UnregisteredError

from app.core.firebase import initialize_firebase, is_firebase_ready
from app.core.logging import get_logger

logger = get_logger(__name__)

INVALID_TOKEN_ERRORS = (
    "NotRegistered",
    "InvalidRegistration",
    "UNREGISTERED",
    "INVALID_ARGUMENT",
    "registration-token-not-registered",
    "Requested entity was not found",
    "SenderId mismatch",
    "mismatched-credential",
    "third-party-auth-error",
)

TOPIC_ALL_USERS = "all_users"
TOPIC_ALL_DRIVERS = "all_drivers"
TOPIC_PROMOTION = "promotion"
TOPIC_NEWS = "news"
TOPIC_MAINTENANCE = "maintenance"


def city_topic(city_id: str) -> str:
    return f"city_{city_id}"


def ride_topic(ride_id: str) -> str:
    return f"ride_{ride_id}"


def _ensure_ready() -> bool:
    if is_firebase_ready():
        return True
    return initialize_firebase()


def _stringify_data(data: dict | None) -> dict[str, str]:
    if not data:
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        out[str(key)] = value if isinstance(value, str) else str(value)
    return out


def _android_config(
    *,
    priority: str = "high",
    ttl_seconds: int | None = 86400,
    channel_id: str = "ride",
    sound: str = "default",
    click_action: str | None = None,
    image: str | None = None,
) -> messaging.AndroidConfig:
    notification = messaging.AndroidNotification(
        sound=sound,
        channel_id=channel_id,
        click_action=click_action or "FLUTTER_NOTIFICATION_CLICK",
        priority="max" if priority == "high" else "default",
        default_vibrate_timings=True,
        image=image,
    )
    return messaging.AndroidConfig(
        priority=priority,
        ttl=ttl_seconds,
        notification=notification,
    )


def _apns_config(
    *,
    sound: str = "default",
    image: str | None = None,
    category: str | None = None,
) -> messaging.APNSConfig:
    headers = {"apns-priority": "10"}
    aps = messaging.Aps(
        sound=sound,
        content_available=True,
        mutable_content=bool(image),
        category=category,
    )
    return messaging.APNSConfig(
        headers=headers,
        payload=messaging.APNSPayload(aps=aps),
        fcm_options=messaging.APNSFCMOptions(image=image) if image else None,
    )


def _is_invalid_token_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}:{exc}".lower()
    return any(token.lower() in text for token in INVALID_TOKEN_ERRORS) or isinstance(
        exc, (UnregisteredError, InvalidArgumentError)
    )


def _build_message(
    *,
    token: str | None = None,
    topic: str | None = None,
    title: str,
    body: str,
    data: dict | None = None,
    image: str | None = None,
    channel_id: str = "ride",
    sound: str = "default",
    priority: str = "high",
    ttl_seconds: int | None = 86400,
    click_action: str | None = None,
    analytics_label: str | None = None,
    data_only: bool = False,
) -> messaging.Message:
    payload = _stringify_data(data)
    if "notification_id" not in payload:
        payload["notification_id"] = str(uuid4())
    if "timestamp" not in payload:
        payload["timestamp"] = str(int(time.time()))
    if "priority" not in payload:
        payload["priority"] = priority

    notification = None if data_only else messaging.Notification(title=title, body=body, image=image)

    return messaging.Message(
        token=token,
        topic=topic,
        notification=notification,
        data=payload,
        android=_android_config(
            priority=priority,
            ttl_seconds=ttl_seconds,
            channel_id=channel_id,
            sound=sound,
            click_action=click_action,
            image=image,
        ),
        apns=_apns_config(sound=sound, image=image),
        fcm_options=messaging.FCMOptions(analytics_label=analytics_label) if analytics_label else None,
    )


def send_to_token(
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
    *,
    image: str | None = None,
    channel_id: str = "ride",
    sound: str = "default",
    priority: str = "high",
    ttl_seconds: int | None = 86400,
    click_action: str | None = None,
    analytics_label: str | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Send a notification to a single device token."""
    if not token or not token.strip():
        return {"success": False, "error": "empty_token", "invalid_token": False}

    if not _ensure_ready():
        logger.warning("fcm_send_skipped_firebase_not_ready", token_prefix=token[:12])
        return {"success": False, "error": "firebase_not_ready", "invalid_token": False}

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            message = _build_message(
                token=token.strip(),
                title=title,
                body=body,
                data=data,
                image=image,
                channel_id=channel_id,
                sound=sound,
                priority=priority,
                ttl_seconds=ttl_seconds,
                click_action=click_action,
                analytics_label=analytics_label,
            )
            message_id = messaging.send(message)
            logger.info(
                "fcm_send_success",
                message_id=message_id,
                token_prefix=token[:12],
                title=title,
                attempt=attempt,
            )
            return {"success": True, "message_id": message_id, "invalid_token": False}
        except Exception as exc:
            invalid = _is_invalid_token_error(exc)
            last_error = str(exc)
            logger.warning(
                "fcm_send_failed",
                error=last_error,
                token_prefix=token[:12],
                attempt=attempt,
                invalid_token=invalid,
                retryable=not invalid and attempt < max_retries,
            )
            if invalid:
                return {"success": False, "error": last_error, "invalid_token": True}
            if attempt >= max_retries:
                break
            time.sleep(0.35 * (attempt + 1))

    return {"success": False, "error": last_error or "unknown", "invalid_token": False}


def send_data_notification(
    token: str,
    data: dict,
    *,
    analytics_label: str | None = None,
    channel_id: str = "ride",
) -> dict[str, Any]:
    """Send a data-only message (no system tray notification on some platforms)."""
    if not _ensure_ready():
        return {"success": False, "error": "firebase_not_ready", "invalid_token": False}
    try:
        message = _build_message(
            token=token,
            title="",
            body="",
            data=data,
            channel_id=channel_id,
            analytics_label=analytics_label,
            data_only=True,
        )
        message_id = messaging.send(message)
        return {"success": True, "message_id": message_id, "invalid_token": False}
    except Exception as exc:
        invalid = _is_invalid_token_error(exc)
        logger.warning("fcm_data_send_failed", error=str(exc), invalid_token=invalid)
        return {"success": False, "error": str(exc), "invalid_token": invalid}


def send_notification(
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Alias for send_to_token — matches the production API surface."""
    return send_to_token(token, title, body, data, **kwargs)


def send_to_multiple_tokens(
    tokens: Iterable[str],
    title: str,
    body: str,
    data: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    unique = [t.strip() for t in tokens if t and str(t).strip()]
    unique = list(dict.fromkeys(unique))
    if not unique:
        return {"success": False, "sent": 0, "failed": 0, "invalid_tokens": []}
    if not _ensure_ready():
        return {"success": False, "sent": 0, "failed": len(unique), "invalid_tokens": [], "error": "firebase_not_ready"}

    # Prefer multicast when available; fall back to sequential sends.
    try:
        payload = _stringify_data(data)
        if "notification_id" not in payload:
            payload["notification_id"] = str(uuid4())
        multicast = messaging.MulticastMessage(
            tokens=unique,
            notification=messaging.Notification(title=title, body=body),
            data=payload,
            android=_android_config(
                priority=kwargs.get("priority", "high"),
                channel_id=kwargs.get("channel_id", "ride"),
                sound=kwargs.get("sound", "default"),
                image=kwargs.get("image"),
            ),
            apns=_apns_config(sound=kwargs.get("sound", "default"), image=kwargs.get("image")),
        )
        response = messaging.send_each_for_multicast(multicast)
        invalid_tokens: list[str] = []
        for idx, send_response in enumerate(response.responses):
            if send_response.success:
                continue
            exc = send_response.exception
            if exc and _is_invalid_token_error(exc):
                invalid_tokens.append(unique[idx])
        logger.info(
            "fcm_multicast_completed",
            success_count=response.success_count,
            failure_count=response.failure_count,
            invalid_count=len(invalid_tokens),
        )
        return {
            "success": response.failure_count == 0,
            "sent": response.success_count,
            "failed": response.failure_count,
            "invalid_tokens": invalid_tokens,
        }
    except Exception as exc:
        logger.warning("fcm_multicast_failed_fallback_sequential", error=str(exc))
        sent = 0
        failed = 0
        invalid_tokens = []
        for token in unique:
            result = send_to_token(token, title, body, data, **kwargs)
            if result.get("success"):
                sent += 1
            else:
                failed += 1
                if result.get("invalid_token"):
                    invalid_tokens.append(token)
        return {
            "success": failed == 0,
            "sent": sent,
            "failed": failed,
            "invalid_tokens": invalid_tokens,
        }


def send_to_topic(
    topic: str,
    title: str,
    body: str,
    data: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if not topic:
        return {"success": False, "error": "empty_topic"}
    if not _ensure_ready():
        return {"success": False, "error": "firebase_not_ready"}
    try:
        message = _build_message(
            topic=topic,
            title=title,
            body=body,
            data=data,
            image=kwargs.get("image"),
            channel_id=kwargs.get("channel_id", "admin"),
            sound=kwargs.get("sound", "default"),
            priority=kwargs.get("priority", "high"),
            ttl_seconds=kwargs.get("ttl_seconds", 86400),
            analytics_label=kwargs.get("analytics_label"),
        )
        message_id = messaging.send(message)
        logger.info("fcm_topic_send_success", topic=topic, message_id=message_id)
        return {"success": True, "message_id": message_id}
    except (FirebaseError, Exception) as exc:
        logger.error("fcm_topic_send_failed", topic=topic, error=str(exc))
        return {"success": False, "error": str(exc)}


def subscribe_token(token: str, topic: str) -> dict[str, Any]:
    if not _ensure_ready():
        return {"success": False, "error": "firebase_not_ready"}
    try:
        response = messaging.subscribe_to_topic([token], topic)
        return {
            "success": response.failure_count == 0,
            "success_count": response.success_count,
            "failure_count": response.failure_count,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def unsubscribe_token(token: str, topic: str) -> dict[str, Any]:
    if not _ensure_ready():
        return {"success": False, "error": "firebase_not_ready"}
    try:
        response = messaging.unsubscribe_from_topic([token], topic)
        return {
            "success": response.failure_count == 0,
            "success_count": response.success_count,
            "failure_count": response.failure_count,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
