# Unified User + Driver Backend Architecture

Single FastAPI backend serving **User Panel** and **Driver Panel** against one PostgreSQL database.

## Technology Stack

| Layer | Choice |
|-------|--------|
| Runtime | Python 3.12 |
| API | FastAPI + Pydantic V2 |
| ORM | SQLAlchemy 2.0 Async |
| Database | PostgreSQL + Alembic |
| Auth | JWT (access + refresh), `token_version` invalidation |
| Cache / Geo | Redis (driver matching GEO) |
| Realtime | WebSockets (`/ws/*`) |
| Rate limit | SlowAPI |
| Containers | Docker Compose |

## Clean Architecture (per domain module)

Each domain under `app/<module>/`:

```
models.py       # SQLAlchemy ORM
schemas.py      # Pydantic request/response
crud.py         # Database access only
service.py      # Business logic
router.py       # HTTP — validate + delegate to service
dependencies.py # FastAPI Depends (auth, DB helpers)
```

Routers **never** contain business logic.

## API Prefixes (spec)

| Prefix | Purpose |
|--------|---------|
| `/api/v1/auth` | Register, login, OTP, refresh, logout |
| `/api/v1/users` | User profile, wallet, saved places (panel APIs) |
| `/api/v1/drivers` | Driver profile, KYC, online/offline, earnings |
| `/api/v1/rides` | **Unified ride lifecycle** (book → complete) |
| `/api/v1/vehicles` | Vehicle types & documents (via common/drivers) |
| `/api/v1/wallet` | Wallet & transactions (planned dedicated router) |
| `/api/v1/payments` | Payment intents (planned dedicated router) |
| `/api/v1/notifications` | Push / in-app (planned dedicated router) |
| `/api/v1/common` | Shared public reads |
| `/api/v1/public` | Unauthenticated (maps search, legal pages) |
| `/api/v1/admin` | Admin panel |

**Legacy aliases** (hidden from Swagger): `/api/v1/user`, `/api/v1/driver` for existing frontends.

## Ride Lifecycle

```
REQUESTED → SEARCHING_DRIVER → DRIVER_ASSIGNED → DRIVER_ARRIVED
  → OTP_VERIFIED → STARTED → IN_PROGRESS → COMPLETED
                                    ↘ CANCELLED
```

Every transition writes a row to `ride_events` (timeline).

## JWT Payload

```json
{
  "sub": "<uuid>",
  "user_id": "<uuid>",
  "role": "USER | DRIVER",
  "token_version": 1,
  "type": "access | refresh"
}
```

Logout increments `token_version` on the account (structure ready).

## Module Build Status

| # | Module | Status |
|---|--------|--------|
| 1 | Project setup | ✅ Done |
| 2 | Database | ✅ 37 tables, Alembic 001–003 |
| 3 | Authentication | ✅ JWT + OTP + `token_version` |
| 4 | Common utilities | ✅ core/, middleware/, maps fallback |
| 5 | User module | ✅ `/users` + legacy `/user` |
| 6 | Driver module | ✅ `/drivers` + legacy `/driver` |
| 7 | Vehicle module | ✅ Models + common/driver APIs |
| 8 | Ride module | ✅ **New** `/api/v1/rides/*` |
| 9 | Driver matching | ✅ Redis GEO service |
| 10 | Wallet | ✅ Models + user/driver wallet APIs |
| 11 | Payments | ⚠️ Gateway stubs (Stripe/Razorpay) |
| 12 | Notifications | ⚠️ DB + Celery stub |
| 13 | Google Maps | ✅ Server-side + OSRM/Nominatim fallback |
| 14 | WebSockets | ✅ `/ws/ride`, `/ws/location` |
| 15 | Testing | 🔄 Started (`tests/`) |
| 16 | Docker | ✅ `docker/docker-compose.yml` |

## Key Ride Endpoints

```
POST /api/v1/rides/estimate
POST /api/v1/rides/book
GET  /api/v1/rides/current
GET  /api/v1/rides/history
GET  /api/v1/rides/{id}
POST /api/v1/rides/{id}/cancel
POST /api/v1/rides/{id}/accept      (driver)
POST /api/v1/rides/{id}/reject      (driver)
POST /api/v1/rides/{id}/arrived     (driver)
POST /api/v1/rides/{id}/verify-otp  (driver)
POST /api/v1/rides/{id}/start       (driver)
POST /api/v1/rides/{id}/complete    (driver)
```

## Running Locally

```bash
cd Backend
alembic upgrade head
uvicorn app.main:app --reload
```

Swagger: http://127.0.0.1:8000/docs

## Next Steps (remaining modules)

1. Dedicated `/wallet`, `/payments`, `/notifications` routers
2. Real payment gateway + webhook handlers
3. Firebase FCM push integration
4. Full pytest coverage per module
5. Consolidate duplicate `api/*` into domain `router.py` files
