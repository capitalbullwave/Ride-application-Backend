"""Persist full driver onboarding data in one transaction."""
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import KYCStatus
from app.core.exceptions import ConflictException
from app.drivers.models import Driver, DriverBankAccount, DriverDocument
from app.vehicles.models import Vehicle
from app.services.image_storage import persist_driver_image
from app.repositories.driver_repository import DriverRepository
from app.schemas.driver import DriverRegistrationComplete


def _mask_account_number(account_number: str) -> str:
    digits = account_number.strip()
    if len(digits) <= 4:
        return digits
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


class DriverRegistrationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.driver_repo = DriverRepository(db)

    async def complete_registration(
        self,
        driver: Driver,
        data: DriverRegistrationComplete,
    ) -> dict:
        if data.email and data.email != driver.email:
            existing = await self.driver_repo.get_by_email(data.email)
            if existing and existing.id != driver.id:
                raise ConflictException("Email already registered")

        driver.first_name = data.first_name
        driver.last_name = data.last_name or ""
        if data.email:
            driver.email = data.email
        driver.license_number = data.license_number
        driver.date_of_birth = data.date_of_birth
        driver.gender = data.gender
        driver.referral_code = data.referral_code
        driver.address_line = data.current_address
        driver.city = data.city
        driver.state = data.state
        driver.country = data.country
        driver.pin_code = data.pin_code
        if data.profile_photo:
            driver.profile_photo = persist_driver_image(
                data.profile_photo, str(driver.id), "selfie"
            )
        driver.kyc_status = KYCStatus.SUBMITTED.value

        await self.driver_repo.update(driver)

        for doc in data.documents:
            if not doc.document_url or not doc.document_url.strip():
                continue
            expiry = doc.expiry_date
            if expiry is None and doc.document_type == "DRIVING_LICENSE" and data.license_expiry_date:
                expiry = datetime.combine(
                    data.license_expiry_date,
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                )
            stored_url = persist_driver_image(
                doc.document_url.strip(),
                str(driver.id),
                doc.document_type.lower(),
            )
            if not stored_url:
                continue
            self.db.add(
                DriverDocument(
                    driver_id=driver.id,
                    document_type=doc.document_type,
                    document_url=stored_url,
                    document_number=doc.document_number,
                    expiry_date=expiry,
                    status=KYCStatus.PENDING.value,
                )
            )

        vehicle_data = data.vehicle
        vehicle = Vehicle(
            driver_id=driver.id,
            vehicle_type_id=vehicle_data.vehicle_type_id,
            license_plate=vehicle_data.license_plate,
            make=vehicle_data.make or vehicle_data.model,
            model=vehicle_data.model,
            color=vehicle_data.color,
            year=vehicle_data.year,
        )
        self.db.add(vehicle)
        await self.db.flush()

        bank_id = None
        if data.bank:
            bank = DriverBankAccount(
                driver_id=driver.id,
                account_holder_name=data.bank.account_holder_name.strip(),
                account_number_masked=_mask_account_number(data.bank.account_number),
                ifsc_code=data.bank.ifsc_code.strip().upper(),
                bank_name=data.bank.bank_name.strip(),
                upi_id=data.bank.upi_id.strip() if data.bank.upi_id else None,
                is_primary=True,
                is_verified=False,
            )
            self.db.add(bank)
            await self.db.flush()
            bank_id = str(bank.id)

        return {
            "driver_id": str(driver.id),
            "vehicle_id": str(vehicle.id),
            "bank_account_id": bank_id,
            "kyc_status": driver.kyc_status,
            "message": "Registration submitted successfully",
        }
