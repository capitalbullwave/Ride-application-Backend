"""Driver wallet ledger — balances and transactions."""
from __future__ import annotations

import uuid
from typing import List, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commission.models import DriverWallet, DriverWalletTransaction
from app.core.constants import DriverWalletTransactionType
from app.core.exceptions import ValidationException


class DriverWalletService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create(self, driver_id: uuid.UUID) -> DriverWallet:
        result = await self.db.execute(
            select(DriverWallet).where(DriverWallet.driver_id == driver_id)
        )
        wallet = result.scalar_one_or_none()
        if wallet:
            return wallet

        wallet = DriverWallet(driver_id=driver_id)
        self.db.add(wallet)
        await self.db.flush()
        return wallet

    async def get_wallet_for_driver(self, driver_id: uuid.UUID) -> DriverWallet:
        return await self.get_or_create(driver_id)

    async def credit_ride_earning(
        self,
        *,
        driver_id: uuid.UUID,
        ride_id: uuid.UUID,
        amount: float,
        description: str,
    ) -> DriverWalletTransaction:
        if amount < 0:
            raise ValidationException("Credit amount cannot be negative")

        wallet = await self.get_or_create(driver_id)
        new_balance = round(float(wallet.available_balance) + amount, 2)
        wallet.available_balance = new_balance
        wallet.lifetime_earnings = round(float(wallet.lifetime_earnings) + amount, 2)

        txn = DriverWalletTransaction(
            driver_id=driver_id,
            ride_id=ride_id,
            wallet_id=wallet.id,
            type=DriverWalletTransactionType.CREDIT.value,
            amount=amount,
            description=description,
            balance_after_transaction=new_balance,
        )
        self.db.add(txn)
        await self.db.flush()
        return txn

    async def credit_bonus(
        self,
        *,
        driver_id: uuid.UUID,
        amount: float,
        description: str,
    ) -> DriverWalletTransaction:
        if amount < 0:
            raise ValidationException("Credit amount cannot be negative")

        wallet = await self.get_or_create(driver_id)
        new_balance = round(float(wallet.available_balance) + amount, 2)
        wallet.available_balance = new_balance
        wallet.lifetime_earnings = round(float(wallet.lifetime_earnings) + amount, 2)

        txn = DriverWalletTransaction(
            driver_id=driver_id,
            ride_id=None,
            wallet_id=wallet.id,
            type=DriverWalletTransactionType.CREDIT.value,
            amount=amount,
            description=description,
            balance_after_transaction=new_balance,
        )
        self.db.add(txn)
        await self.db.flush()
        return txn

    async def admin_credit(
        self,
        *,
        driver_id: uuid.UUID,
        amount: float,
        note: str | None = None,
    ) -> DriverWalletTransaction:
        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValidationException("Amount must be greater than zero")
        description = (note or "").strip() or "Admin wallet credit"
        wallet = await self.get_or_create(driver_id)
        new_balance = round(float(wallet.available_balance) + amount, 2)
        wallet.available_balance = new_balance

        txn = DriverWalletTransaction(
            driver_id=driver_id,
            ride_id=None,
            wallet_id=wallet.id,
            type=DriverWalletTransactionType.ADJUSTMENT.value,
            amount=amount,
            description=description[:500],
            balance_after_transaction=new_balance,
        )
        self.db.add(txn)
        await self.db.flush()
        return txn

    async def list_transactions(
        self,
        driver_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[DriverWalletTransaction], int]:
        wallet = await self.get_or_create(driver_id)
        count_result = await self.db.execute(
            select(func.count())
            .select_from(DriverWalletTransaction)
            .where(DriverWalletTransaction.wallet_id == wallet.id)
        )
        total = int(count_result.scalar_one() or 0)

        result = await self.db.execute(
            select(DriverWalletTransaction)
            .where(DriverWalletTransaction.wallet_id == wallet.id)
            .order_by(DriverWalletTransaction.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def has_ride_credit(self, ride_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(DriverWalletTransaction.id)
            .where(
                DriverWalletTransaction.ride_id == ride_id,
                DriverWalletTransaction.type == DriverWalletTransactionType.CREDIT.value,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
