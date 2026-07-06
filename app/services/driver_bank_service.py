"""Driver bank account management."""
import re
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationException
from app.drivers.models import DriverBankAccount
from app.schemas.driver import DriverBankResponse, DriverBankUpsert


def _mask_account_number(account_number: str) -> str:
    digits = re.sub(r"\D", "", account_number)
    if len(digits) >= 4:
        return f"****{digits[-4:]}"
    return "****"


def bank_to_response(bank: DriverBankAccount) -> DriverBankResponse:
    return DriverBankResponse(
        account_holder=bank.account_holder_name,
        account_number=bank.account_number_masked,
        ifsc=bank.ifsc_code,
        bank_name=bank.bank_name,
        upi_id=bank.upi_id,
    )


class DriverBankService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_primary(self, driver_id: uuid.UUID) -> DriverBankAccount | None:
        result = await self.db.execute(
            select(DriverBankAccount).where(
                DriverBankAccount.driver_id == driver_id,
                DriverBankAccount.is_primary.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, driver_id: uuid.UUID, data: DriverBankUpsert) -> DriverBankResponse:
        await self.db.execute(
            update(DriverBankAccount)
            .where(DriverBankAccount.driver_id == driver_id)
            .values(is_primary=False)
        )

        if data.payment_type == "upi":
            if not data.upi_id:
                raise ValidationException("UPI ID is required")
            upi = data.upi_id.strip()
            masked = f"UPI:{upi[-4:]}" if len(upi) >= 4 else "UPI"
            bank = DriverBankAccount(
                driver_id=driver_id,
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
            bank = DriverBankAccount(
                driver_id=driver_id,
                account_holder_name=data.account_holder_name.strip(),
                account_number_masked=_mask_account_number(data.account_number),
                ifsc_code=data.ifsc_code.strip().upper(),
                bank_name=data.bank_name.strip(),
                upi_id=data.upi_id.strip() if data.upi_id else None,
                is_primary=True,
            )

        self.db.add(bank)
        await self.db.flush()
        return bank_to_response(bank)
