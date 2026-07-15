"""Admin finance: overview stats, withdrawals, refunds, wallet activity."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commission.models import DriverWallet, DriverWalletTransaction
from app.core.constants import (
    DriverWalletTransactionType,
    PaymentStatus,
    RideStatus,
    WalletTransactionType,
    WithdrawalStatus,
)
from app.core.exceptions import NotFoundException, ValidationException
from app.drivers.models import Driver, DriverBankAccount
from app.models import Payment, Ride, User, UserBankAccount, Wallet, WalletTransaction, WithdrawalRequest
from app.services.commission_service import CommissionService
from app.services.driver_wallet_service import DriverWalletService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _status_lower(value: str | None) -> str:
    return (value or "").strip().lower() or "pending"


class AdminFinanceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def overview(self) -> dict:
        completed = Ride.status == RideStatus.COMPLETED.value

        total_revenue = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(Ride.final_fare), 0.0)).where(completed)
                )
            ).scalar_one()
            or 0
        )
        platform_commission = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(Ride.company_earning), 0.0)).where(completed)
                )
            ).scalar_one()
            or 0
        )
        driver_earnings = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(Ride.driver_earning), 0.0)).where(completed)
                )
            ).scalar_one()
            or 0
        )

        pending_withdrawals = (
            await self.db.execute(
                select(
                    func.coalesce(func.sum(WithdrawalRequest.amount), 0.0),
                    func.count(WithdrawalRequest.id),
                ).where(WithdrawalRequest.status == WithdrawalStatus.PENDING.value)
            )
        ).one()
        pending_payout_amount = float(pending_withdrawals[0] or 0)
        pending_payout_count = int(pending_withdrawals[1] or 0)

        # If no formal withdrawal requests yet, show unpaid driver wallet balances
        if pending_payout_count == 0:
            wallet_pending = (
                await self.db.execute(
                    select(
                        func.coalesce(func.sum(DriverWallet.available_balance), 0.0),
                        func.count(DriverWallet.id),
                    ).where(DriverWallet.available_balance > 0)
                )
            ).one()
            pending_payout_amount = float(wallet_pending[0] or 0)
            pending_payout_count = int(wallet_pending[1] or 0)

        now = _utc_now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_start.month == 1:
            prev_month_start = month_start.replace(year=month_start.year - 1, month=12)
        else:
            prev_month_start = month_start.replace(month=month_start.month - 1)

        this_month_rev = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(Ride.final_fare), 0.0)).where(
                        completed,
                        Ride.completed_at >= month_start,
                    )
                )
            ).scalar_one()
            or 0
        )
        last_month_rev = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(Ride.final_fare), 0.0)).where(
                        completed,
                        Ride.completed_at >= prev_month_start,
                        Ride.completed_at < month_start,
                    )
                )
            ).scalar_one()
            or 0
        )
        if last_month_rev > 0:
            change_pct = round(((this_month_rev - last_month_rev) / last_month_rev) * 100, 1)
            revenue_change = f"{'+' if change_pct >= 0 else ''}{change_pct}% this month"
            change_type = "positive" if change_pct >= 0 else "negative"
        elif this_month_rev > 0:
            revenue_change = "New this month"
            change_type = "positive"
        else:
            revenue_change = "No revenue this month"
            change_type = "neutral"

        commission = await CommissionService(self.db).get_vehicle_settings_response()
        driver_pct = float(commission.default_commission_percentage)
        platform_pct = round(100 - driver_pct, 2)

        this_month_commission = float(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(Ride.company_earning), 0.0)).where(
                        completed,
                        Ride.completed_at >= month_start,
                    )
                )
            ).scalar_one()
            or 0
        )

        pending_withdrawal_reqs = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(WithdrawalRequest)
                    .where(WithdrawalRequest.status == WithdrawalStatus.PENDING.value)
                )
            ).scalar_one()
            or 0
        )
        pending_refunds = len(await self._pending_refund_rows())

        return {
            "totalRevenue": total_revenue,
            "platformCommission": platform_commission,
            "driverEarnings": driver_earnings,
            "pendingPayouts": pending_payout_amount,
            "pendingPayoutCount": pending_payout_count,
            "pendingApprovalsCount": pending_withdrawal_reqs + pending_refunds,
            "pendingWithdrawalRequests": pending_withdrawal_reqs,
            "pendingRefundRequests": pending_refunds,
            "revenueChange": revenue_change,
            "revenueChangeType": change_type,
            "platformFeePercent": platform_pct,
            "driverSharePercent": driver_pct,
            "thisMonthRevenue": this_month_rev,
            "thisMonthCommission": this_month_commission,
        }

    async def commission_report(self) -> dict:
        overview = await self.overview()
        now = _utc_now()
        months: list[dict] = []
        for i in range(5, -1, -1):
            # Approximate month windows walking back from current month start
            cursor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            year, month = cursor.year, cursor.month - i
            while month <= 0:
                month += 12
                year -= 1
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            if month == 12:
                end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

            row = (
                await self.db.execute(
                    select(
                        func.coalesce(func.sum(Ride.final_fare), 0.0),
                        func.coalesce(func.sum(Ride.company_earning), 0.0),
                        func.count(Ride.id),
                    ).where(
                        Ride.status == RideStatus.COMPLETED.value,
                        Ride.completed_at >= start,
                        Ride.completed_at < end,
                    )
                )
            ).one()
            months.append(
                {
                    "month": start.strftime("%B"),
                    "year": start.year,
                    "revenue": float(row[0] or 0),
                    "commission": float(row[1] or 0),
                    "rides": int(row[2] or 0),
                }
            )

        return {
            "totalCommissionYtd": overview["platformCommission"],
            "commissionRate": overview["platformFeePercent"],
            "thisMonthCommission": overview["thisMonthCommission"],
            "months": months,
        }

    def _map_withdrawal(
        self,
        wr: WithdrawalRequest,
        *,
        driver: Driver | None = None,
        user: User | None = None,
        driver_bank: DriverBankAccount | None = None,
        user_bank: UserBankAccount | None = None,
    ) -> dict:
        is_user = wr.user_id is not None
        if is_user and user:
            name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.phone
            public_id = user.public_id
            party_id = str(wr.user_id)
        elif driver:
            name = f"{driver.first_name or ''} {driver.last_name or ''}".strip()
            public_id = driver.public_id
            party_id = str(wr.driver_id)
        else:
            name = "User" if is_user else "Driver"
            public_id = None
            party_id = str(wr.user_id or wr.driver_id or "")

        bank = user_bank if is_user else driver_bank
        method = "Bank Transfer"
        bank_details = None
        if bank:
            if getattr(bank, "upi_id", None) and (getattr(bank, "bank_name", None) or "") == "UPI":
                method = f"UPI ({bank.upi_id})"
            elif bank.upi_id and not bank.bank_name:
                method = f"UPI ({bank.upi_id})"
            elif bank.bank_name:
                method = bank.bank_name

            account_number = None
            if hasattr(bank, "account_number") and bank.account_number:
                account_number = bank.account_number
            else:
                account_number = getattr(bank, "account_number_masked", None)

            bank_details = {
                "accountHolder": bank.account_holder_name,
                "accountNumber": account_number,
                "ifsc": bank.ifsc_code,
                "bankName": bank.bank_name,
                "upiId": bank.upi_id or "",
            }

        return {
            "id": str(wr.id),
            "party": "user" if is_user else "driver",
            "driverId": str(wr.driver_id) if wr.driver_id else None,
            "userId": str(wr.user_id) if wr.user_id else None,
            "driverPublicId": public_id if not is_user else None,
            "userPublicId": public_id if is_user else None,
            "driverName": name if not is_user else None,
            "userName": name if is_user else None,
            "partyName": name,
            "partyId": party_id,
            "partyPublicId": public_id,
            "amount": float(wr.amount),
            "status": _status_lower(wr.status),
            "date": (wr.processed_at or wr.created_at).isoformat()
            if (wr.processed_at or wr.created_at)
            else None,
            "createdAt": wr.created_at.isoformat() if wr.created_at else None,
            "processedAt": wr.processed_at.isoformat() if wr.processed_at else None,
            "method": method,
            "rejectionReason": wr.rejection_reason,
            "bankDetails": bank_details,
        }

    async def list_payouts(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        limit: int = 50,
        party: str | None = None,
    ) -> dict:
        query = (
            select(WithdrawalRequest, Driver, User, DriverBankAccount, UserBankAccount)
            .outerjoin(Driver, Driver.id == WithdrawalRequest.driver_id)
            .outerjoin(User, User.id == WithdrawalRequest.user_id)
            .outerjoin(DriverBankAccount, DriverBankAccount.id == WithdrawalRequest.bank_account_id)
            .outerjoin(UserBankAccount, UserBankAccount.id == WithdrawalRequest.user_bank_account_id)
            .order_by(WithdrawalRequest.created_at.desc())
        )
        if status and status.lower() != "all":
            query = query.where(WithdrawalRequest.status == status.strip().upper())
        party_f = (party or "all").lower()
        if party_f == "user":
            query = query.where(WithdrawalRequest.user_id.is_not(None))
        elif party_f == "driver":
            query = query.where(WithdrawalRequest.driver_id.is_not(None))

        count_q = select(func.count()).select_from(WithdrawalRequest)
        if status and status.lower() != "all":
            count_q = count_q.where(WithdrawalRequest.status == status.strip().upper())
        if party_f == "user":
            count_q = count_q.where(WithdrawalRequest.user_id.is_not(None))
        elif party_f == "driver":
            count_q = count_q.where(WithdrawalRequest.driver_id.is_not(None))
        total = int((await self.db.execute(count_q)).scalar_one() or 0)

        result = await self.db.execute(query.offset((page - 1) * limit).limit(limit))
        items = [
            self._map_withdrawal(
                wr, driver=driver, user=user, driver_bank=d_bank, user_bank=u_bank
            )
            for wr, driver, user, d_bank, u_bank in result.all()
        ]
        return {"items": items, "total": total}

    async def process_payout(self, payout_id: UUID, admin_id: UUID) -> dict:
        wr = await self.db.get(WithdrawalRequest, payout_id)
        if not wr:
            raise NotFoundException("Payout request not found")
        if wr.status != WithdrawalStatus.PENDING.value:
            raise ValidationException("Only pending payouts can be processed")

        amount = float(wr.amount)

        if wr.user_id:
            # User balance already debited on request — just mark paid
            wr.status = WithdrawalStatus.PAID.value
            wr.processed_by = admin_id
            wr.processed_at = _utc_now()
            await self.db.flush()
            user = await self.db.get(User, wr.user_id)
            bank = (
                await self.db.get(UserBankAccount, wr.user_bank_account_id)
                if wr.user_bank_account_id
                else None
            )
            return self._map_withdrawal(wr, user=user, user_bank=bank)

        if not wr.driver_id:
            raise ValidationException("Invalid payout request")

        wallet_svc = DriverWalletService(self.db)
        driver_wallet = await wallet_svc.get_or_create(wr.driver_id)

        # Funds were moved to pending_balance on request; settle them now
        if float(driver_wallet.pending_balance) < amount - 0.01:
            if float(driver_wallet.available_balance) < amount:
                raise ValidationException("Insufficient driver wallet balance")
            driver_wallet.available_balance = round(
                float(driver_wallet.available_balance) - amount, 2
            )
        else:
            driver_wallet.pending_balance = round(
                float(driver_wallet.pending_balance) - amount, 2
            )

        self.db.add(
            DriverWalletTransaction(
                driver_id=wr.driver_id,
                ride_id=None,
                wallet_id=driver_wallet.id,
                type=DriverWalletTransactionType.WITHDRAWAL.value,
                amount=amount,
                description=f"Withdrawal payout {wr.id}",
                balance_after_transaction=float(driver_wallet.available_balance),
            )
        )

        wr.status = WithdrawalStatus.PAID.value
        wr.processed_by = admin_id
        wr.processed_at = _utc_now()
        await self.db.flush()

        driver = await self.db.get(Driver, wr.driver_id)
        bank = await self.db.get(DriverBankAccount, wr.bank_account_id) if wr.bank_account_id else None
        return self._map_withdrawal(wr, driver=driver, driver_bank=bank)

    async def reject_payout(
        self, payout_id: UUID, admin_id: UUID, reason: str | None = None
    ) -> dict:
        wr = await self.db.get(WithdrawalRequest, payout_id)
        if not wr:
            raise NotFoundException("Payout request not found")
        if wr.status != WithdrawalStatus.PENDING.value:
            raise ValidationException("Only pending payouts can be rejected")

        amount = float(wr.amount)

        if wr.user_id:
            # Restore user wallet balance
            wallet = await self.db.get(Wallet, wr.wallet_id)
            if wallet:
                balance_before = float(wallet.balance)
                wallet.balance = round(balance_before + amount, 2)
                self.db.add(
                    WalletTransaction(
                        wallet_id=wallet.id,
                        transaction_type=WalletTransactionType.CREDIT.value,
                        amount=amount,
                        balance_before=balance_before,
                        balance_after=wallet.balance,
                        description="Withdrawal rejected — amount restored",
                        reference_id=str(wr.id),
                        reference_type="withdrawal",
                    )
                )
            wr.status = WithdrawalStatus.REJECTED.value
            wr.rejection_reason = (reason or "Rejected by admin").strip()[:500]
            wr.processed_by = admin_id
            wr.processed_at = _utc_now()
            await self.db.flush()
            user = await self.db.get(User, wr.user_id)
            bank = (
                await self.db.get(UserBankAccount, wr.user_bank_account_id)
                if wr.user_bank_account_id
                else None
            )
            return self._map_withdrawal(wr, user=user, user_bank=bank)

        if not wr.driver_id:
            raise ValidationException("Invalid payout request")

        wallet_svc = DriverWalletService(self.db)
        driver_wallet = await wallet_svc.get_or_create(wr.driver_id)

        if float(driver_wallet.pending_balance) >= amount - 0.01:
            driver_wallet.pending_balance = round(
                float(driver_wallet.pending_balance) - amount, 2
            )
            driver_wallet.available_balance = round(
                float(driver_wallet.available_balance) + amount, 2
            )

        wr.status = WithdrawalStatus.REJECTED.value
        wr.rejection_reason = (reason or "Rejected by admin").strip()[:500]
        wr.processed_by = admin_id
        wr.processed_at = _utc_now()
        await self.db.flush()

        driver = await self.db.get(Driver, wr.driver_id)
        bank = await self.db.get(DriverBankAccount, wr.bank_account_id) if wr.bank_account_id else None
        return self._map_withdrawal(wr, driver=driver, driver_bank=bank)

    async def process_all_pending(self, admin_id: UUID) -> dict:
        result = await self.db.execute(
            select(WithdrawalRequest.id).where(
                WithdrawalRequest.status == WithdrawalStatus.PENDING.value
            )
        )
        ids = list(result.scalars().all())
        processed = 0
        errors: list[str] = []
        for wid in ids:
            try:
                await self.process_payout(wid, admin_id)
                processed += 1
            except Exception as exc:  # noqa: BLE001 — collect failures for batch
                errors.append(f"{wid}: {exc}")
        return {"processed": processed, "failed": len(errors), "errors": errors[:20]}

    async def list_refunds(self, *, page: int = 1, limit: int = 50) -> dict:
        """Completed payment refunds + wallet REFUND ledger rows."""
        items: list[dict] = []

        pay_result = await self.db.execute(
            select(Payment, User, Ride)
            .join(User, User.id == Payment.user_id)
            .outerjoin(Ride, Ride.id == Payment.ride_id)
            .where(
                or_(
                    Payment.status == PaymentStatus.REFUNDED.value,
                    Payment.refund_amount > 0,
                )
            )
            .order_by(Payment.created_at.desc())
        )
        for payment, user, ride in pay_result.all():
            amount = float(payment.refund_amount or payment.amount or 0)
            items.append(
                {
                    "id": str(payment.id),
                    "rideId": ride.public_id if ride else str(payment.ride_id),
                    "rideUuid": str(payment.ride_id),
                    "user": f"{user.first_name or ''} {user.last_name or ''}".strip() or user.phone,
                    "userId": str(user.id),
                    "amount": amount,
                    "reason": (ride.cancellation_reason if ride else None) or "Payment refund",
                    "status": "completed",
                    "date": (
                        payment.refunded_at or payment.created_at
                    ).isoformat()
                    if (payment.refunded_at or payment.created_at)
                    else None,
                    "source": "payment",
                }
            )

        # Wallet REFUND transactions (user wallets)
        tx_result = await self.db.execute(
            select(WalletTransaction, Wallet, User)
            .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
            .outerjoin(User, User.id == Wallet.user_id)
            .where(
                func.upper(WalletTransaction.transaction_type)
                == WalletTransactionType.REFUND.value
            )
            .order_by(WalletTransaction.created_at.desc())
            .limit(200)
        )
        seen_ids = {i["id"] for i in items}
        for tx, wallet, user in tx_result.all():
            if str(tx.id) in seen_ids:
                continue
            ride_label = tx.reference_id if tx.reference_type == "ride" else None
            items.append(
                {
                    "id": str(tx.id),
                    "rideId": ride_label or "—",
                    "rideUuid": ride_label,
                    "user": (
                        f"{user.first_name or ''} {user.last_name or ''}".strip()
                        if user
                        else ("Driver" if wallet.driver_id else "User")
                    ),
                    "userId": str(wallet.user_id) if wallet.user_id else None,
                    "amount": float(tx.amount),
                    "reason": tx.description or "Wallet refund",
                    "status": "completed",
                    "date": tx.created_at.isoformat() if tx.created_at else None,
                    "source": "wallet",
                }
            )

        items.sort(key=lambda x: x.get("date") or "", reverse=True)
        total = len(items)
        start = (page - 1) * limit
        return {"items": items[start : start + limit], "total": total}

    async def list_wallet_transactions(
        self,
        *,
        owner: str = "user",
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        query = (
            select(WalletTransaction, Wallet, User, Driver)
            .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
            .outerjoin(User, User.id == Wallet.user_id)
            .outerjoin(Driver, Driver.id == Wallet.driver_id)
            .order_by(WalletTransaction.created_at.desc())
        )
        if owner == "user":
            query = query.where(Wallet.user_id.is_not(None))
        elif owner == "driver":
            query = query.where(Wallet.driver_id.is_not(None))

        count_base = select(func.count()).select_from(WalletTransaction).join(
            Wallet, Wallet.id == WalletTransaction.wallet_id
        )
        if owner == "user":
            count_base = count_base.where(Wallet.user_id.is_not(None))
        elif owner == "driver":
            count_base = count_base.where(Wallet.driver_id.is_not(None))
        total = int((await self.db.execute(count_base)).scalar_one() or 0)

        result = await self.db.execute(query.offset((page - 1) * limit).limit(limit))
        items = []
        for tx, wallet, user, driver in result.all():
            if user:
                owner_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.phone
            elif driver:
                owner_name = f"{driver.first_name or ''} {driver.last_name or ''}".strip()
            else:
                owner_name = "—"
            tx_type = (tx.transaction_type or "").lower()
            items.append(
                {
                    "id": str(tx.id),
                    "user": owner_name,
                    "userId": str(wallet.user_id) if wallet.user_id else None,
                    "driverId": str(wallet.driver_id) if wallet.driver_id else None,
                    "type": tx_type,
                    "description": tx.description,
                    "amount": float(tx.amount),
                    "status": "completed",
                    "date": tx.created_at.isoformat() if tx.created_at else None,
                }
            )
        return {"items": items, "total": total}

    async def create_driver_withdrawal(
        self, driver: Driver, amount: float
    ) -> WithdrawalRequest:
        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValidationException("Withdrawal amount must be greater than zero")

        from app.services.driver_bank_service import DriverBankService
        from app.services.payment_service import WalletService

        bank = await DriverBankService(self.db).get_primary(driver.id)
        if not bank:
            raise ValidationException("Add a bank account before withdrawing")

        wallet_svc = DriverWalletService(self.db)
        driver_wallet = await wallet_svc.get_or_create(driver.id)
        if float(driver_wallet.available_balance) < amount:
            raise ValidationException("Insufficient available balance")

        # WithdrawalRequest.wallet_id FK → wallets table
        legacy = await WalletService(self.db).get_or_create_wallet(driver_id=driver.id)

        driver_wallet.available_balance = round(
            float(driver_wallet.available_balance) - amount, 2
        )
        driver_wallet.pending_balance = round(
            float(driver_wallet.pending_balance) + amount, 2
        )

        wr = WithdrawalRequest(
            driver_id=driver.id,
            wallet_id=legacy.id,
            bank_account_id=bank.id,
            amount=amount,
            status=WithdrawalStatus.PENDING.value,
        )
        self.db.add(wr)
        await self.db.flush()
        return wr

    async def _pending_refund_rows(self) -> list[tuple]:
        """Cancelled rides with completed payment not yet refunded."""
        result = await self.db.execute(
            select(Payment, User, Ride)
            .join(User, User.id == Payment.user_id)
            .join(Ride, Ride.id == Payment.ride_id)
            .where(
                Ride.status == RideStatus.CANCELLED.value,
                Payment.status == PaymentStatus.COMPLETED.value,
                Payment.refund_amount <= 0,
            )
            .order_by(Payment.created_at.desc())
            .limit(200)
        )
        rows = []
        for payment, user, ride in result.all():
            gw = payment.gateway_response if isinstance(payment.gateway_response, dict) else {}
            if gw.get("admin_refund_rejected"):
                continue
            rows.append((payment, user, ride))
        return rows

    def _activity_item(
        self,
        *,
        id: str,
        party: str,
        category: str,
        type: str,
        title: str,
        amount: float,
        status: str,
        date: str | None,
        partyName: str,
        partyId: str | None = None,
        reference: str | None = None,
        actionable: bool = False,
    ) -> dict:
        return {
            "id": id,
            "party": party,
            "category": category,
            "type": type,
            "title": title,
            "amount": float(amount),
            "status": status,
            "date": date,
            "partyName": partyName,
            "partyId": partyId,
            "reference": reference,
            "actionable": actionable,
        }

    async def list_activity(
        self,
        *,
        party: str | None = None,
        category: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """Unified user + driver financial activity feed."""
        items: list[dict] = []
        party_f = (party or "all").lower()
        category_f = (category or "all").lower()

        # User / legacy wallet ledger
        if party_f in ("all", "user", "driver"):
            tx_q = (
                select(WalletTransaction, Wallet, User, Driver)
                .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
                .outerjoin(User, User.id == Wallet.user_id)
                .outerjoin(Driver, Driver.id == Wallet.driver_id)
                .order_by(WalletTransaction.created_at.desc())
                .limit(300)
            )
            if party_f == "user":
                tx_q = tx_q.where(Wallet.user_id.is_not(None))
            elif party_f == "driver":
                tx_q = tx_q.where(Wallet.driver_id.is_not(None))

            for tx, wallet, user, driver in (await self.db.execute(tx_q)).all():
                if user:
                    p_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.phone
                    p_party = "user"
                    p_id = str(user.id)
                elif driver:
                    p_name = f"{driver.first_name or ''} {driver.last_name or ''}".strip()
                    p_party = "driver"
                    p_id = str(driver.id)
                else:
                    continue

                tx_type = (tx.transaction_type or "").lower()
                cat = "refund" if tx_type == "refund" else "wallet"
                if (tx.reference_type or "").upper() == "REFERRAL" or "referral" in (
                    tx.description or ""
                ).lower():
                    cat = "referral"
                items.append(
                    self._activity_item(
                        id=f"wtx-{tx.id}",
                        party=p_party,
                        category=cat,
                        type=tx_type,
                        title=tx.description or tx_type,
                        amount=float(tx.amount),
                        status="completed",
                        date=tx.created_at.isoformat() if tx.created_at else None,
                        partyName=p_name,
                        partyId=p_id,
                        reference=tx.reference_id,
                    )
                )

        # Driver commission wallet ledger
        if party_f in ("all", "driver"):
            dw_q = (
                select(DriverWalletTransaction, Driver)
                .join(Driver, Driver.id == DriverWalletTransaction.driver_id)
                .order_by(DriverWalletTransaction.created_at.desc())
                .limit(300)
            )
            for tx, driver in (await self.db.execute(dw_q)).all():
                name = f"{driver.first_name or ''} {driver.last_name or ''}".strip()
                tx_type = (tx.type or "").lower()
                cat = "payout" if tx_type == "withdrawal" else "earning"
                items.append(
                    self._activity_item(
                        id=f"dwt-{tx.id}",
                        party="driver",
                        category=cat,
                        type=tx_type,
                        title=tx.description or tx_type,
                        amount=float(tx.amount),
                        status="completed",
                        date=tx.created_at.isoformat() if tx.created_at else None,
                        partyName=name,
                        partyId=str(driver.id),
                        reference=str(tx.ride_id) if tx.ride_id else None,
                    )
                )

        # Withdrawal requests (user + driver)
        if party_f in ("all", "driver", "user") and category_f in ("all", "payout"):
            wr_q = (
                select(WithdrawalRequest, Driver, User)
                .outerjoin(Driver, Driver.id == WithdrawalRequest.driver_id)
                .outerjoin(User, User.id == WithdrawalRequest.user_id)
                .order_by(WithdrawalRequest.created_at.desc())
                .limit(200)
            )
            if party_f == "user":
                wr_q = wr_q.where(WithdrawalRequest.user_id.is_not(None))
            elif party_f == "driver":
                wr_q = wr_q.where(WithdrawalRequest.driver_id.is_not(None))

            for wr, driver, user in (await self.db.execute(wr_q)).all():
                if wr.user_id:
                    name = (
                        f"{user.first_name or ''} {user.last_name or ''}".strip() or user.phone
                        if user
                        else "User"
                    )
                    p_party = "user"
                    p_id = str(wr.user_id)
                else:
                    name = (
                        f"{driver.first_name or ''} {driver.last_name or ''}".strip()
                        if driver
                        else "Driver"
                    )
                    p_party = "driver"
                    p_id = str(wr.driver_id) if wr.driver_id else None
                st = _status_lower(wr.status)
                items.append(
                    self._activity_item(
                        id=f"wd-{wr.id}",
                        party=p_party,
                        category="payout",
                        type="withdrawal",
                        title=f"Withdrawal request — {st}",
                        amount=float(wr.amount),
                        status=st if st != "paid" else "completed",
                        date=wr.created_at.isoformat() if wr.created_at else None,
                        partyName=name,
                        partyId=p_id,
                        actionable=st == "pending",
                    )
                )

        # Ride payments
        if party_f in ("all", "user") and category_f in ("all", "payment", "refund"):
            pay_q = (
                select(Payment, User, Ride)
                .join(User, User.id == Payment.user_id)
                .outerjoin(Ride, Ride.id == Payment.ride_id)
                .order_by(Payment.created_at.desc())
                .limit(200)
            )
            for payment, user, ride in (await self.db.execute(pay_q)).all():
                name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.phone
                st = _status_lower(payment.status)
                is_refund = st == "refunded" or float(payment.refund_amount or 0) > 0
                if category_f == "payment" and is_refund:
                    continue
                if category_f == "refund" and not is_refund:
                    continue
                items.append(
                    self._activity_item(
                        id=f"pay-{payment.id}",
                        party="user",
                        category="refund" if is_refund else "payment",
                        type=payment.payment_method or "payment",
                        title=(
                            f"Refund — ride {ride.public_id}"
                            if is_refund and ride
                            else (
                                f"Ride payment — {ride.public_id}"
                                if ride
                                else "Ride payment"
                            )
                        ),
                        amount=float(
                            payment.refund_amount
                            if is_refund and payment.refund_amount
                            else payment.amount
                        ),
                        status="completed" if is_refund or st == "completed" else st,
                        date=payment.created_at.isoformat() if payment.created_at else None,
                        partyName=name,
                        partyId=str(user.id),
                        reference=ride.public_id if ride else str(payment.ride_id),
                    )
                )

        if category_f != "all":
            items = [i for i in items if i["category"] == category_f]

        items.sort(key=lambda x: x.get("date") or "", reverse=True)
        total = len(items)
        start = (page - 1) * limit
        return {"items": items[start : start + limit], "total": total}

    async def list_approvals(self) -> dict:
        """Everything admin must pay / approve from Finance, plus payout history."""
        payouts_res = await self.list_payouts(status="PENDING", page=1, limit=100)
        paid_res = await self.list_payouts(status="PAID", page=1, limit=100)
        rejected_res = await self.list_payouts(status="REJECTED", page=1, limit=100)

        history = sorted(
            [*paid_res["items"], *rejected_res["items"]],
            key=lambda x: x.get("processedAt") or x.get("date") or x.get("createdAt") or "",
            reverse=True,
        )

        refund_items = []
        for payment, user, ride in await self._pending_refund_rows():
            refund_items.append(
                {
                    "id": str(payment.id),
                    "rideId": ride.public_id if ride else str(payment.ride_id),
                    "rideUuid": str(payment.ride_id),
                    "user": f"{user.first_name or ''} {user.last_name or ''}".strip()
                    or user.phone,
                    "userId": str(user.id),
                    "amount": float(payment.amount),
                    "reason": ride.cancellation_reason or "Ride cancelled — refund due",
                    "status": "pending",
                    "date": payment.created_at.isoformat() if payment.created_at else None,
                    "source": "payment",
                    "paymentMethod": payment.payment_method,
                }
            )
        return {
            "payouts": payouts_res["items"],
            "refunds": refund_items,
            "history": history,
            "paidCount": len(paid_res["items"]),
            "rejectedCount": len(rejected_res["items"]),
            "pendingPayouts": len(payouts_res["items"]),
            "pendingRefunds": len(refund_items),
            "totalPending": len(payouts_res["items"]) + len(refund_items),
        }

    async def approve_refund(self, payment_id: UUID, admin_id: UUID) -> dict:
        payment = await self.db.get(Payment, payment_id)
        if not payment:
            raise NotFoundException("Payment not found")
        if payment.status == PaymentStatus.REFUNDED.value or float(payment.refund_amount or 0) > 0:
            raise ValidationException("Refund already processed")
        if payment.status != PaymentStatus.COMPLETED.value:
            raise ValidationException("Only completed payments can be refunded")

        from app.services.payment_service import WalletService

        amount = round(float(payment.amount), 2)
        wallet_svc = WalletService(self.db)
        wallet = await wallet_svc.get_or_create_wallet(user_id=payment.user_id)

        balance_before = float(wallet.balance)
        wallet.balance = round(balance_before + amount, 2)
        txn = WalletTransaction(
            wallet_id=wallet.id,
            transaction_type=WalletTransactionType.REFUND.value,
            amount=amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            description=f"Admin refund for ride payment {payment.id}",
            reference_id=str(payment.ride_id),
            reference_type="ride",
        )
        self.db.add(txn)

        payment.status = PaymentStatus.REFUNDED.value
        payment.refund_amount = amount
        payment.refunded_at = _utc_now()
        gw = dict(payment.gateway_response or {}) if isinstance(payment.gateway_response, dict) else {}
        gw["admin_refund_by"] = str(admin_id)
        gw["admin_refund_at"] = _utc_now().isoformat()
        payment.gateway_response = gw
        await self.db.flush()

        user = await self.db.get(User, payment.user_id)
        ride = await self.db.get(Ride, payment.ride_id)
        return {
            "id": str(payment.id),
            "status": "completed",
            "amount": amount,
            "user": (
                f"{user.first_name or ''} {user.last_name or ''}".strip()
                if user
                else str(payment.user_id)
            ),
            "rideId": ride.public_id if ride else str(payment.ride_id),
        }

    async def reject_refund(self, payment_id: UUID, admin_id: UUID, reason: str | None = None) -> dict:
        payment = await self.db.get(Payment, payment_id)
        if not payment:
            raise NotFoundException("Payment not found")
        if payment.status == PaymentStatus.REFUNDED.value or float(payment.refund_amount or 0) > 0:
            raise ValidationException("Refund already processed")

        gw = dict(payment.gateway_response or {}) if isinstance(payment.gateway_response, dict) else {}
        gw["admin_refund_rejected"] = True
        gw["admin_refund_rejected_by"] = str(admin_id)
        gw["admin_refund_reject_reason"] = (reason or "Rejected by admin")[:500]
        payment.gateway_response = gw
        await self.db.flush()
        return {"id": str(payment.id), "status": "rejected"}
