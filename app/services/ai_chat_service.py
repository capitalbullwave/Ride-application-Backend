"""Bullwave Assistant — project-aware guest chatbot with live fare tools."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.maps.service import MapsService
from app.models import VehicleType
from app.rides.schemas import RideEstimateRequest
from app.rides.service import FareEngine

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are Bullwave Assistant for Bull Wave Rides (also called Wave Go / Bullwave Rides).
You help GUESTS and riders with clear, accurate answers about THIS product.

Language rule (important):
- If the user writes in English → reply in clear professional English.
- If the user writes in Hindi or Hinglish → reply in natural Hinglish/Hindi.
- Do NOT default to Hinglish when the user asked in English.
- UI prompts are English; still match the user's message language.

Be warm, clear, and practical. Use short paragraphs or plain bullet lines with "• ".
Never invent fake live prices — call tools for fares.
You may help without login (guest mode).

Formatting rules (strict):
- Do NOT use Markdown. No **, __, ##, ###, or # headings.
- Do NOT use the word "major".
- Prefer clean plain text that looks professional in a chat bubble.

════════════════════════════════════
PRODUCT FEATURES
════════════════════════════════════
• Bike — fast, affordable city hops
• Electric Auto — easy auto rides, clear pricing
• Cab — comfortable car rides
• Parcel — pickup → drop delivery with tracking
• Ambulance / Emergency — medical transport request flow
• Rental — hour-based packages (not distance fare)
• Travel and Stay — travel-oriented booking entry
• Upfront fare estimate before booking
• Multi-stop rides (up to 3 stops)
• Scheduled rides
• Payments: Cash, Wallet, UPI, Card
• Wallet top-up, coupons/promos
• Subscriptions with ride discount %
• Student Pass (Aadhaar + student ID → admin approval → discount)
• Refer & Earn
• Saved places, ride history, live tracking, in-ride chat
• Help & Support tickets + FAQs
• AI helpers: Fare Predictor (best time to book), Route Optimizer (smoother routes), Safety Monitor (unusual trip patterns)

════════════════════════════════════
SAFETY (EVERYONE)
════════════════════════════════════
• Live trip tracking and verified captain info
• SOS on an active ride → urgent support ticket, alerts to rider/admin/driver, SMS to emergency contact with location
• Share Ride (client share sheet: driver, plate, destination, Maps link, ride ID)
• Emergency contact name/phone on profile (used for Safety Mode / SOS SMS)
• Insurance / ride protection messaging on eligible rides (marketing safety page)

════════════════════════════════════
WOMEN SAFETY
════════════════════════════════════
1) Prefer Women Captains
   - Female riders can request women captains only
   - Matching filters to female drivers
   - If none nearby → rider can continue with all captains

2) Women Safety Mode (auto for female gender on book)
   - Not the same as women-captain preference
   - Notifies emergency contact (SMS when Twilio is configured) with route + ride ID
   - Push/in-app “Safety Mode Enabled”; admin also notified
   - On trip: Share Ride + SOS; safety check (“Are you safe?”) can escalate to SOS

3) SOS during ride
   - Marks emergency, opens urgent ticket with route/Maps links
   - SMS emergency contact with live location
   - Notifies rider, admin, driver

Guests: explain how it works; say full SOS/Safety Mode needs a booked/active ride and profile emergency contact after signup/login.

════════════════════════════════════
FARE HOW-IT-WORKS
════════════════════════════════════
For normal rides (not rental):
  billable_km = max(0, distance_km − included_distance_km)  # included often ~2 km per vehicle
  subtotal = base_fare + (billable_km × per_km_rate)
  night surcharge may apply (late night / early morning multiplier)
  peak/surge only if active (estimates often show 0)
  platform fee & tax are currently 0 in user-facing estimate
  final = max(subtotal + night + peak − discounts, minimum_fare)

Rental: base package + extra hours × per_hour_rate (distance not charged).
Member/student discounts apply when logged in.
Waiting charges can apply mid-trip and are separate from the upfront estimate.

When the user gives TWO places OR a distance in km and asks fare → ALWAYS call estimate_fare.
When they ask rates/pricing tables → call list_vehicle_rates.
Explain the breakdown (base, distance fare, night if any) in simple words + ₹ amounts from the tool.

════════════════════════════════════
BOOKING TIPS FOR GUESTS
════════════════════════════════════
To book: open Bull Wave Rides → pick service → set pickup & drop → see estimate → login/signup if needed → confirm.
Ambulance uses the Emergency / Ambulance flow.
Do not invent admin tools or backend secrets.
If unsure, say what you know and suggest Help & Support or the Safety page.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "estimate_fare",
            "description": (
                "Estimate live Bullwave fares for all active vehicle types. "
                "Call when user asks fare/price and gives distance_km and/or pickup+dropoff place names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "distance_km": {
                        "type": "number",
                        "description": "Trip distance in kilometers if the user already gave it",
                    },
                    "duration_min": {
                        "type": "number",
                        "description": "Optional duration in minutes",
                    },
                    "pickup": {
                        "type": "string",
                        "description": "Pickup place name or address in India",
                    },
                    "dropoff": {
                        "type": "string",
                        "description": "Dropoff place name or address in India",
                    },
                    "service_group": {
                        "type": "string",
                        "enum": ["ride", "rental"],
                        "description": "Default ride",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_vehicle_rates",
            "description": "List active vehicle types with base fare, per-km rate, included km, minimum fare.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

FALLBACK_DEFAULT = (
    "I’m Bullwave Assistant. I can help you with:\n\n"
    "• Features (Bike, Auto, Cab, Parcel, Ambulance, Wallet…)\n"
    "• Safety / SOS\n"
    "• Women Safety Mode and Prefer Women Captains\n"
    "• Fare estimates (share 2 places or distance in km)"
)


def _strip_markdown(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s+", "• ", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _prefers_hinglish(text: str) -> bool:
    lower = text.lower()
    if re.search(r"[\u0900-\u097F]", text):
        return True
    markers = (
        "kya",
        "kaise",
        "kitna",
        "kaun",
        "hai",
        "hain",
        "batao",
        "lagega",
        "poochh",
        "kiraya",
        "mahila",
        "suraksha",
        "ka ",
        " ke ",
        " se ",
    )
    return any(m in lower for m in markers)


def _extract_distance_km(text: str) -> float | None:
    patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:km|kilometer|kilometers|kms)\b",
        r"(\d+(?:\.\d+)?)\s*(?:किमी|किलोमीटर)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _extract_places(text: str) -> tuple[str | None, str | None]:
    """Parse casual 'A se B' / 'A to B' place pairs from guest messages."""
    cleaned = re.sub(
        r"\b(fare|price|kitna|lagega|hoga|estimate|ka|ki|ke|the|for|please|batao|bata)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?!.")
    match = re.search(
        r"(.+?)\s+(?:se|to|from|->|→|upto|till)\s+(.+)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    pickup = match.group(1).strip(" ,.-")
    dropoff = match.group(2).strip(" ,.-")
    if len(pickup) < 2 or len(dropoff) < 2:
        return None, None
    return pickup, dropoff


class AiChatService:
    OPENAI_URL = "https://api.openai.com/v1/chat/completions"

    @property
    def is_configured(self) -> bool:
        return bool(settings.openai_api_key.strip())

    async def reply(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        db: AsyncSession | None = None,
    ) -> str:
        cleaned = message.strip()
        if not cleaned:
            return _strip_markdown(FALLBACK_DEFAULT)

        if db is not None and self._looks_like_fare_question(cleaned):
            pickup, dropoff = _extract_places(cleaned)
            tool_result = await self._tool_estimate_fare(
                db,
                {
                    "distance_km": _extract_distance_km(cleaned),
                    "pickup": pickup,
                    "dropoff": dropoff,
                },
                user_text=cleaned,
            )
            if tool_result.get("ok") and tool_result.get("estimates"):
                return _strip_markdown(
                    self._format_fare_summary(tool_result, user_text=cleaned)
                )

        if not self.is_configured:
            logger.warning("openai_not_configured")
            return _strip_markdown(await self._offline_reply(cleaned, db))

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in (history or [])[-8:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content[:1600]})
        messages.append({"role": "user", "content": cleaned[:1600]})

        try:
            return _strip_markdown(await self._chat_with_tools(messages, db))
        except Exception as exc:
            logger.warning("openai_chat_error", error=str(exc))
            return _strip_markdown(await self._offline_reply(cleaned, db))

    def _looks_like_fare_question(self, text: str) -> bool:
        lower = text.lower()
        has_fare_intent = any(
            k in lower
            for k in (
                "fare",
                "price",
                "cost",
                "kitna",
                "lagega",
                "kiraya",
                "किराया",
                "भाव",
                "charge",
                "estimate",
            )
        )
        if not has_fare_intent:
            return False
        pickup, dropoff = _extract_places(text)
        return _extract_distance_km(text) is not None or bool(pickup and dropoff)

    async def _chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        db: AsyncSession | None,
    ) -> str:
        async with httpx.AsyncClient(timeout=45.0) as client:
            for _ in range(3):
                response = await client.post(
                    self.OPENAI_URL,
                    headers={
                        "Authorization": f"Bearer {settings.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.openai_model or "gpt-4o-mini",
                        "messages": messages,
                        "tools": TOOLS,
                        "tool_choice": "auto",
                        "temperature": 0.4,
                        "max_tokens": 900,
                    },
                )
                if response.status_code >= 400:
                    logger.warning(
                        "openai_chat_failed",
                        status=response.status_code,
                        body=response.text[:400],
                    )
                    last_user = next(
                        (m["content"] for m in reversed(messages) if m.get("role") == "user"),
                        "",
                    )
                    return await self._offline_reply(str(last_user), db)

                data = response.json()
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                tool_calls = msg.get("tool_calls") or []

                if not tool_calls:
                    content = (msg.get("content") or "").strip()
                    return content or FALLBACK_DEFAULT

                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content"),
                        "tool_calls": tool_calls,
                    }
                )

                for call in tool_calls:
                    fn = call.get("function") or {}
                    name = fn.get("name") or ""
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    if name == "estimate_fare" and db is not None:
                        result = await self._tool_estimate_fare(db, args)
                    elif name == "list_vehicle_rates" and db is not None:
                        result = await self._tool_list_rates(db)
                    else:
                        result = {
                            "ok": False,
                            "error": "Tool unavailable right now. Explain using general fare rules.",
                        }

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(result, ensure_ascii=False)[:6000],
                        }
                    )

        return FALLBACK_DEFAULT

    async def _tool_list_rates(self, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(
            select(VehicleType)
            .where(VehicleType.is_active == True)  # noqa: E712
            .order_by(VehicleType.display_order, VehicleType.name)
        )
        rows = []
        for vt in result.scalars().all():
            rows.append(
                {
                    "name": vt.name,
                    "service_group": vt.service_group or "ride",
                    "base_fare": float(vt.base_fare or 0),
                    "per_km_rate": float(vt.per_km_rate or 0),
                    "included_distance_km": float(getattr(vt, "included_distance_km", 0) or 0),
                    "minimum_fare": float(getattr(vt, "minimum_fare", 0) or 0),
                    "per_hour_rate": float(getattr(vt, "per_hour_rate", 0) or 0),
                    "included_hours": float(getattr(vt, "included_hours", 0) or 0),
                }
            )
        return {"ok": True, "vehicles": rows}

    async def _tool_estimate_fare(
        self,
        db: AsyncSession,
        args: dict[str, Any],
        user_text: str | None = None,
    ) -> dict[str, Any]:
        distance_km = args.get("distance_km")
        duration_min = args.get("duration_min")
        pickup = (args.get("pickup") or "").strip()
        dropoff = (args.get("dropoff") or "").strip()
        service_group = (args.get("service_group") or "ride").strip().lower()

        if distance_km is None and user_text:
            distance_km = _extract_distance_km(user_text)

        pickup_lat = pickup_lng = dropoff_lat = dropoff_lng = None
        route_note = None

        if pickup and dropoff:
            maps = MapsService()
            route = await maps.get_route_between(pickup, dropoff)
            if not route:
                return {
                    "ok": False,
                    "error": f"Could not find a route between '{pickup}' and '{dropoff}'. Ask user to rephrase places.",
                }
            pickup_lat = route["pickup"]["lat"]
            pickup_lng = route["pickup"]["lng"]
            dropoff_lat = route["dropoff"]["lat"]
            dropoff_lng = route["dropoff"]["lng"]
            if distance_km is None:
                distance_km = float(route["distance_km"])
            if duration_min is None:
                duration_min = float(route["duration_min"])
            route_note = {
                "pickup": route["pickup"].get("address") or pickup,
                "dropoff": route["dropoff"].get("address") or dropoff,
                "distance_km": round(float(distance_km), 2),
                "duration_min": round(float(duration_min or 0), 1),
            }
        elif distance_km is not None:
            # Dummy Delhi coords — distance override is used by FareEngine.
            pickup_lat, pickup_lng = 28.6139, 77.2090
            dropoff_lat, dropoff_lng = 28.5355, 77.3910
            route_note = {
                "distance_km": round(float(distance_km), 2),
                "duration_min": round(
                    float(duration_min)
                    if duration_min is not None
                    else FareEngine.estimate_duration_min(float(distance_km)),
                    1,
                ),
                "note": "Estimated from distance only (road path not resolved).",
            }
        else:
            return {
                "ok": False,
                "error": "Need either distance_km or both pickup and dropoff place names.",
            }

        try:
            payload = RideEstimateRequest(
                pickup_lat=float(pickup_lat),
                pickup_lng=float(pickup_lng),
                dropoff_lat=float(dropoff_lat),
                dropoff_lng=float(dropoff_lng),
                distance_km=float(distance_km) if distance_km is not None else None,
                duration_min=float(duration_min) if duration_min is not None else None,
                service_group=service_group,
            )
            estimate = await FareEngine(db).estimate(payload, user_id=None)
        except Exception as exc:
            logger.warning("ai_fare_estimate_failed", error=str(exc))
            return {"ok": False, "error": f"Fare estimate failed: {exc}"}

        return {
            "ok": True,
            "route": route_note,
            "distance_km": estimate.distance_km,
            "duration_min": estimate.duration_min,
            "formula": (
                "billable_km = max(0, distance - included_km); "
                "fare ≈ base_fare + billable_km × per_km_rate (+ night if applicable)"
            ),
            "estimates": [
                {
                    "name": vt.name,
                    "estimated_fare": vt.estimated_fare,
                    "base_fare": vt.base_fare,
                    "distance_fare": vt.distance_fare,
                    "night_charges": vt.night_charges,
                    "peak_charges": vt.peak_charges,
                }
                for vt in estimate.vehicle_types
            ],
        }

    def _format_fare_summary(
        self,
        tool_result: dict[str, Any],
        user_text: str = "",
    ) -> str:
        route = tool_result.get("route") or {}
        hinglish = _prefers_hinglish(user_text) if user_text else False

        if hinglish:
            lines = ["Yeh approximate Bull Wave fare estimate hai (guest / before login):\n"]
        else:
            lines = ["Here’s an approximate Bull Wave fare estimate (guest / before login):\n"]

        if route.get("pickup") and route.get("dropoff"):
            lines.append(f"• Route: {route['pickup']} → {route['dropoff']}")
        lines.append(
            f"• Distance: ~{tool_result.get('distance_km')} km"
            f" · ETA: ~{tool_result.get('duration_min')} min"
        )
        lines.append("")
        lines.append("Vehicle-wise fare:" if not hinglish else "Vehicle-wise fare:")
        for vt in tool_result.get("estimates") or []:
            lines.append(
                f"• {vt['name']}: ₹{vt['estimated_fare']}"
                f" (base ₹{vt['base_fare']} + distance ₹{vt['distance_fare']}"
                + (
                    f" + night ₹{vt['night_charges']}"
                    if float(vt.get("night_charges") or 0) > 0
                    else ""
                )
                + ")"
            )
        lines.append("")
        if hinglish:
            lines.append(
                "Formula: included km ke baad hi per-km charge; night surcharge time pe apply ho sakta hai. "
                "Final fare book karte waqt thoda differ kar sakta hai (traffic / waiting / offers)."
            )
        else:
            lines.append(
                "Formula: per-km applies after the included distance; a night surcharge may apply. "
                "Final fare at booking can vary slightly (traffic, waiting, or offers)."
            )
        return "\n".join(lines)

    async def _offline_reply(self, text: str, db: AsyncSession | None) -> str:
        lower = text.lower()
        hinglish = _prefers_hinglish(text)

        if any(k in lower for k in ("fare", "price", "kitna", "किराया", "estimate", "km", "किमी", "lagega")):
            if db is not None:
                pickup, dropoff = _extract_places(text)
                result = await self._tool_estimate_fare(
                    db,
                    {
                        "distance_km": _extract_distance_km(text),
                        "pickup": pickup,
                        "dropoff": dropoff,
                    },
                    user_text=text,
                )
                if result.get("ok") and result.get("estimates"):
                    return self._format_fare_summary(result, user_text=text)
            if hinglish:
                return (
                    "Fare aise banta hai:\n"
                    "• base fare + (distance − included km) × per-km rate\n"
                    "• night time pe surcharge ho sakta hai\n\n"
                    "Exact estimate: “10 km ka fare” ya “X se Y kitna lagega?”"
                )
            return (
                "Here’s how fare works:\n"
                "• base fare + (distance − included km) × per-km rate\n"
                "• a night surcharge may apply\n\n"
                "For an exact estimate, ask: “What’s the fare for 10 km?” or “Fare from X to Y?”"
            )

        if any(k in lower for k in ("women", "woman", "mahila", "female", "lady")):
            if hinglish:
                return (
                    "Women Safety Bull Wave pe 2 tareeke se kaam karti hai:\n\n"
                    "1) Prefer Women Captains — female riders women captains request kar sakti hain; "
                    "agar nearby na milen to all captains ke saath continue kar sakti hain.\n\n"
                    "2) Women Safety Mode — female riders ke book par auto-enable: emergency contact ko "
                    "route/ride alert, Share Ride + SOS trip par.\n\n"
                    "Profile me emergency contact zaroor add karein."
                )
            return (
                "Women Safety on Bull Wave works in two ways:\n\n"
                "1) Prefer Women Captains — female riders can request women captains; "
                "if none are nearby, they can continue with all captains.\n\n"
                "2) Women Safety Mode — auto-enabled for female riders on booking: "
                "emergency-contact alert, Share Ride + SOS on the trip.\n\n"
                "Add an emergency contact in your profile."
            )

        if any(k in lower for k in ("safe", "sos", "safety", "emergency", "suraksha")):
            if hinglish:
                return (
                    "Safety features:\n"
                    "• Live tracking + verified captain details\n"
                    "• Share Ride\n"
                    "• SOS on active ride → ticket + alerts + emergency SMS\n"
                    "• Women Safety Mode + Prefer Women Captains\n"
                    "• Ambulance / Emergency\n\n"
                    "Full SOS ke liye login + active ride chahiye."
                )
            return (
                "Safety features:\n"
                "• Live tracking and verified captain details\n"
                "• Share Ride (driver, plate, destination, Maps link)\n"
                "• SOS on an active ride → support ticket, alerts, and emergency-contact SMS\n"
                "• Women Safety Mode and Prefer Women Captains\n"
                "• Ambulance / Emergency medical transport\n\n"
                "Full SOS needs login and an active ride."
            )

        if any(
            k in lower
            for k in (
                "feature",
                "service",
                "kya kya",
                "what can",
                "bike",
                "parcel",
                "ambulance",
                "wallet",
            )
        ):
            if hinglish:
                return (
                    "Bull Wave Rides features:\n"
                    "• Bike, Electric Auto, Cab\n"
                    "• Parcel delivery\n"
                    "• Ambulance / Emergency\n"
                    "• Rentals, Wallet, coupons, subscriptions, student pass, refer & earn\n"
                    "• Live tracking, SOS, women safety, AI helpers\n\n"
                    "Fare ke liye 2 jagah ya km batao."
                )
            return (
                "Bull Wave Rides features:\n"
                "• Bike, Electric Auto, Cab\n"
                "• Parcel delivery\n"
                "• Ambulance / Emergency\n"
                "• Rentals, Travel & Stay entry\n"
                "• Upfront fare, multi-stop, schedule\n"
                "• Wallet, UPI/Cash/Card, coupons, subscriptions, student pass, refer & earn\n"
                "• Live tracking, SOS, women safety, AI fare/route/safety helpers\n\n"
                "For fares, share 2 places or distance in km."
            )

        return FALLBACK_DEFAULT
