# API Migration Guide (v1 → v2)

This document maps **old panel-prefixed endpoints** to the **new modular architecture**.

## Base URL Change

| Old | New |
|-----|-----|
| `/api/v1/admin-panel/*` | `/api/v1/admin/*` |
| `/api/v1/user-panel/*` | `/api/v1/user/*` + `/api/v1/auth/*` + `/api/v1/common/*` + `/api/v1/public/*` |
| `/api/v1/driver-panel/*` | `/api/v1/driver/*` + `/api/v1/auth/*` |
| `/ws/{token}` | `/ws/ride?token=`, `/ws/location?token=`, `/ws/chat?token=`, `/ws/notification?token=` (legacy `/ws/{token}` still works) |

---

## Authentication

| Old | New | Notes |
|-----|-----|-------|
| `POST /user-panel/auth/login` | `POST /auth/login` | Add `"role": "user"` |
| `POST /driver-panel/auth/login` | `POST /auth/login` | Add `"role": "driver"` |
| `POST /admin-panel/auth/login` | `POST /admin/login` | Admin-specific response format |
| `POST /user-panel/auth/register` | `POST /auth/register` | Add `"role": "user"` |
| `POST /driver-panel/auth/register` | `POST /auth/register` | Add `"role": "driver"` |
| `POST */auth/refresh` | `POST /auth/refresh-token` | |
| `POST */auth/logout` | `POST /auth/logout` | User token |
| `POST /admin-panel/auth/logout` | `POST /admin/logout` | Admin token |
| `POST */auth/forgot-password` | `POST /auth/forgot-password?role=user\|driver` | |
| `POST */auth/reset-password` | `POST /auth/reset-password?role=user\|driver` | |
| `POST */auth/login/otp/send` | `POST /auth/send-otp` | `"purpose": "login"` |
| `POST */auth/register/otp/send` | `POST /auth/send-otp` | `"purpose": "register"` |
| `POST */auth/login/otp/verify` | `POST /auth/verify-otp` | |
| `GET /user-panel/auth/me` | `GET /auth/me` | |

---

## User Module

| Old | New |
|-----|-----|
| `GET /user-panel/profile` | `GET /user/profile` |
| `PATCH /user-panel/profile` | `PUT /user/profile` |
| `GET /user-panel/profile/addresses` | `GET /user/saved-address` |
| `POST /user-panel/rides/book` | `POST /user/book-ride` |
| `GET /user-panel/rides/history` | `GET /user/rides` |
| `GET /user-panel/rides/{id}` | `GET /user/ride/{id}` |
| `POST /user-panel/rides/{id}/cancel` | `POST /user/cancel-ride` |
| `GET /user-panel/wallet/balance` | `GET /user/wallet` |
| `GET /user-panel/wallet/transactions` | `GET /user/transactions` |
| `POST /user-panel/wallet/add-money` | `POST /user/payment` |
| `GET /user-panel/notifications` | `GET /user/notifications` |
| `POST /user-panel/support/tickets` | `POST /user/support` |
| `GET /user-panel/home/dashboard` | `GET /user/home/dashboard` (legacy alias) |
| `GET /user-panel/rides/{id}/driver` | `GET /user/rides/{id}/driver` (legacy alias) |

---

## Driver Module

| Old | New |
|-----|-----|
| `GET /driver-panel/drivers/me` | `GET /driver/profile` |
| `PUT /driver-panel/drivers/me` | `PUT /driver/profile` |
| `POST /driver-panel/drivers/status` (online) | `PUT /driver/go-online` |
| `POST /driver-panel/drivers/status` (offline) | `PUT /driver/go-offline` |
| `GET /driver-panel/drivers/rides/available` | `GET /driver/ride-requests` |
| `POST /driver-panel/rides/{id}/accept` | `POST /driver/accept-ride` |
| `POST /driver-panel/rides/{id}/start` | `POST /driver/start-ride` |
| `POST /driver-panel/rides/{id}/complete` | `POST /driver/end-ride` |
| `GET /driver-panel/drivers/earnings` | `GET /driver/earnings` |
| `POST /driver-panel/drivers/location` | `POST /driver/location` (legacy alias) |

---

## Admin Module

| Old | New |
|-----|-----|
| `POST /admin-panel/auth/login` | `POST /admin/login` |
| `GET /admin-panel/auth/me` | `GET /admin/me` |
| `GET /admin-panel/dashboard/stats` | `GET /admin/dashboard/stats` |
| `GET /admin-panel/dashboard/charts` | `GET /admin/dashboard/charts` |
| `GET /admin-panel/users` | `GET /admin/users` |
| `GET /admin-panel/drivers` | `GET /admin/drivers` |
| `GET /admin-panel/rides` | `GET /admin/rides` |
| `GET /admin-panel/finance/transactions` | `GET /admin/payments` or `/admin/finance/transactions` |
| `POST /admin-panel/drivers/{id}/approve` | `PUT /admin/approve-driver` |
| `POST /admin-panel/users/{id}/block` | `PUT /admin/block-user` |
| `GET /admin-panel/settings` | `GET /admin/settings` |
| `PATCH /admin-panel/settings` | `PUT /admin/pricing` (pricing subset) |

---

## Common Module

| Old | New |
|-----|-----|
| `GET /user-panel/home/vehicle-categories` | `GET /common/vehicle-types` |
| `GET /user-panel/banners` | `GET /common/banners` |
| `GET /user-panel/support/faqs` | `GET /common/support/faqs` |
| `GET /admin-panel/vehicle-categories` | `GET /admin/vehicle-categories` (admin-managed) |

---

## Public Module

| Old | New |
|-----|-----|
| `GET /user-panel/settings/page/privacy-policy` | `GET /public/privacy-policy` |
| `GET /user-panel/settings/page/terms` | `GET /public/terms` |
| `GET /user-panel/settings/general` | `GET /public/about` + `/public/contact` |

---

## WebSocket

| Old | New |
|-----|-----|
| `WS /ws/{token}` | `WS /ws/ride?token={jwt}` |
| Ride events | `WS /ws/ride?token={jwt}` |
| Location updates | `WS /ws/location?token={jwt}` |
| Chat | `WS /ws/chat?token={jwt}` |
| Notifications | `WS /ws/notification?token={jwt}` |

---

## Swagger Sections

OpenAPI now groups endpoints under:

1. **Authentication** — `/api/v1/auth`
2. **User** — `/api/v1/user`
3. **Driver** — `/api/v1/driver`
4. **Admin** — `/api/v1/admin`
5. **Common** — `/api/v1/common`
6. **Public** — `/api/v1/public`
7. **WebSocket** — `/ws`

---

## RBAC

| Role | Allowed prefixes |
|------|------------------|
| USER | `/api/v1/user/*`, `/api/v1/auth/*`, `/api/v1/common/*`, `/api/v1/public/*` |
| DRIVER | `/api/v1/driver/*`, `/api/v1/auth/*`, `/api/v1/common/*`, `/api/v1/public/*` |
| ADMIN | `/api/v1/admin/*`, `/api/v1/common/*`, `/api/v1/public/*` |
| Public | `/api/v1/public/*`, `/api/v1/common/*` (read-only public data) |

---

## Folder Structure

```
app/
├── api/
│   ├── auth/       routes.py, service.py, repository.py, schemas.py, dependencies.py
│   ├── user/
│   ├── driver/
│   ├── admin/
│   ├── common/
│   ├── public/
│   ├── websocket/
│   └── router.py
├── auth/           service.py, dependencies.py (domain layer)
├── core/
├── database/
├── models/
├── schemas/
├── repositories/
├── services/
└── main.py
```

Removed: `panels/`, `users/`, `drivers/`, `rides/`, `wallet/`, `admin/compat_*`
