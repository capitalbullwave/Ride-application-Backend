"""User bank account for wallet withdrawals."""
from __future__ import annotations

import re
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import WalletTransactionType, WithdrawalStatus
from app.core.exceptions import NotFoundException, ValidationException
from app.models import User, UserBankAccount, Wallet, WalletTransaction, WithdrawalRequest
from app.services.payment_service import WalletService


def _mask_account_number(account_number: str) -> str:
    digits = re.sub(r"\D", "", account_number)
    if len(digits) >= 4:
        return f"****{digits[-4:]}"
    return "****"


class UserBankUpsert(BaseModel):
    payment_type: Literal["bank", "upi"] = "bank"
    account_holder_name: str = Field(..., min_length=2, max_length=150)
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    bank_name: Optional[str] = None
    upi_id: Optional[str] = None


def user_bank_to_dict(bank: UserBankAccount) -> dict:
    return {
        "account_holder": bank.account_holder_name,
        "account_number": bank.account_number_masked,
        "ifsc": bank.ifsc_code,
        "bank_name": bank.bank_name,
        "upi_id": bank.upi_id,
        "is_verified": bank.is_verified,
    }


class UserBankService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_primary(self, user_id: uuid.UUID) -> UserBankAccount | None:
        result = await self.db.execute(
            select(UserBankAccount).where(
                UserBankAccount.user_id == user_id,
                UserBankAccount.is_primary.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, user_id: uuid.UUID, data: UserBankUpsert) -> UserBankAccount:
        await self.db.execute(
            update(UserBankAccount)
            .where(UserBankAccount.user_id == user_id)
            .values(is_primary=False)
        )

        if data.payment_type == "upi":
            if not data.upi_id:
                raise ValidationException("UPI ID is required")
            upi = data.upi_id.strip()
            masked = f"UPI:{upi[-4:]}" if len(upi) >= 4 else "UPI"
            bank = UserBankAccount(
                user_id=user_id,
                account_holder_name=data.account_holder_name.strip(),
                account_number_masked=masked,
                ifsc_code="UPI0000000",
                bank_name="UPI",
                upi_id=upi,
                is_primary=True,
            )
        else:
            if not data.account_number or not data.ifsc_code or not data.bank_name:
                raise ValidationException("Bank account details are incomplete")
            bank = UserBankAccount(
                user_id=user_id,
                account_holder_name=data.account_holder_name.strip(),
                account_number_masked=_mask_account_number(data.account_number),
                ifsc_code=data.ifsc_code.strip().upper(),
                bank_name=data.bank_name.strip(),
                upi_id=data.upi_id.strip() if data.upi_id else None,
                is_primary=True,
            )

        self.db.add(bank)
        await self.db.flush()
        return bank


class UserWithdrawalService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user: User, amount: float) -> WithdrawalRequest:
        amount = round(float(amount), 2)
        if amount < 1:
            raise ValidationException("Minimum withdrawal amount is ₹1")

        bank = await UserBankService(self.db).get_primary(user.id)
        if not bank:
            raise ValidationException("Add a bank account or UPI before withdrawing")

        wallet = await WalletService(self.db).get_or_create_wallet(user_id=user.id)
        if float(wallet.balance) < amount:
            raise ValidationException("Insufficient wallet balance")

        # Prevent overlapping pending requests eating the same balance twice
        pending = await self.db.scalar(
            select(WithdrawalRequest.id).where(
                WithdrawalRequest.user_id == user.id,
                WithdrawalRequest.status == WithdrawalStatus.PENDING.value,
            )
        )
        if pending:
            raise ValidationException("You already have a pending withdrawal request")

        balance_before = float(wallet.balance)
        wallet.balance = round(balance_before - amount, 2)
        self.db.add(
            WalletTransaction(
                wallet_id=wallet.id,
                transaction_type=WalletTransactionType.DEBIT.value,
                amount=amount,
                balance_before=balance_before,
                balance_after=wallet.balance,
                description="Withdrawal request (pending admin approval)",
                reference_type="withdrawal",
            )
        )

        wr = WithdrawalRequest(
            user_id=user.id,
            driver_id=None,
            wallet_id=wallet.id,
            bank_account_id=None,
            user_bank_account_id=bank.id,
            amount=amount,
            status=WithdrawalStatus.PENDING.value,
        )
        self.db.add(wr)
        await self.db.flush()
        # Link reference after id exists
        # (optional) update last txn reference_id
        return wr
