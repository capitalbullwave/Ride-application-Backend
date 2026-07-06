"""Step-by-step driver onboarding (Rapido-style registration flow)."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import KYCStatus
from app.core.exceptions import ValidationException
from app.drivers.models import Driver, DriverDocument
from app.repositories.driver_repository import DriverRepository
from app.schemas.driver import (
    AccountItemStatus,
    DriverRegistrationProgressResponse,
    DriverSavedRegistrationData,
    RegistrationStepInfo,
    SavedDocumentInfo,
    SaveKycStep,
    SaveLicenseNumber,
    SaveLicenseUpload,
    SaveProfileStep,
    SaveVehicleNumberStep,
)
from app.services.driver_bank_service import DriverBankService
from app.services.image_storage import persist_driver_image
from app.vehicles.models import Vehicle, VehicleType


class DriverRegistrationProgressService:
    LICENSE_FRONT = "DRIVING_LICENSE"
    LICENSE_BACK = "DRIVING_LICENSE_BACK"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.driver_repo = DriverRepository(db)

    async def _documents_for(self, driver_id: UUID) -> list[DriverDocument]:
        result = await self.db.execute(
            select(DriverDocument).where(DriverDocument.driver_id == driver_id)
        )
        return list(result.scalars().all())

    async def _vehicle_for(self, driver_id: UUID) -> Vehicle | None:
        result = await self.db.execute(
            select(Vehicle).where(Vehicle.driver_id == driver_id).limit(1)
        )
        return result.scalar_one_or_none()

    def _doc_url(self, documents: list[DriverDocument], doc_type: str) -> str | None:
        for doc in documents:
            if doc.document_type == doc_type and doc.document_url:
                return doc.document_url
        return None

    def _doc_status(self, documents: list[DriverDocument], doc_type: str) -> str:
        for doc in documents:
            if doc.document_type == doc_type:
                return doc.status or KYCStatus.PENDING.value
        return "pending"

    async def get_progress(self, driver: Driver) -> DriverRegistrationProgressResponse:
        documents = await self._documents_for(driver.id)
        vehicle = await self._vehicle_for(driver.id)

        license_front = self._doc_url(documents, self.LICENSE_FRONT)
        license_number = (driver.license_number or "").strip()
        license_done = bool(license_front) and license_number not in ("", "PENDING")

        profile_done = bool((driver.first_name or "").strip()) and bool(
            driver.profile_photo
        )

        vehicle_done = bool(vehicle and (vehicle.license_plate or "").strip())
        vehicle_type_label = vehicle.model if vehicle else None

        aadhaar_done = bool(self._doc_url(documents, "AADHAAR"))
        pan_done = bool(self._doc_url(documents, "PAN"))
        kyc_done = aadhaar_done or pan_done

        submitted = driver.kyc_status in (
            KYCStatus.SUBMITTED.value,
            KYCStatus.APPROVED.value,
        )

        def step_status(completed: bool, doc_types: list[str] | None = None) -> str:
            if completed and submitted:
                for doc_type in doc_types or []:
                    status = self._doc_status(documents, doc_type)
                    if status == KYCStatus.PENDING.value:
                        return "under_verification"
                return "completed"
            if completed:
                return "completed"
            return "pending"

        steps = [
            RegistrationStepInfo(
                id="vehicle",
                completed=vehicle is not None,
                status=step_status(vehicle is not None),
                subtitle="Selected" if vehicle_type_label else None,
            ),
            RegistrationStepInfo(
                id="license",
                completed=license_done,
                status=step_status(
                    license_done,
                    [self.LICENSE_FRONT, self.LICENSE_BACK],
                ),
            ),
            RegistrationStepInfo(
                id="photo_name",
                completed=profile_done,
                status=step_status(profile_done),
            ),
            RegistrationStepInfo(
                id="vehicle_number",
                completed=vehicle_done,
                status=step_status(vehicle_done, ["VEHICLE_RC"]),
            ),
            RegistrationStepInfo(
                id="kyc",
                completed=kyc_done,
                status=step_status(kyc_done, ["AADHAAR", "PAN"]),
            ),
        ]

        steps_by_id = {step.id: step for step in steps}

        def step_verified(step_id: str) -> bool:
            step = steps_by_id.get(step_id)
            if not step:
                return False
            return (
                step.completed
                and step.status == "completed"
                and driver.kyc_status == KYCStatus.APPROVED.value
            )

        def doc_verified(doc_type: str) -> bool:
            for doc in documents:
                if doc.document_type == doc_type:
                    if not doc.document_url:
                        return False
                    if doc.status == KYCStatus.APPROVED.value:
                        return True
                    return driver.kyc_status == KYCStatus.APPROVED.value
            return False

        bank = await DriverBankService(self.db).get_primary(driver.id)
        bank_verified = bank is not None

        account_items = [
            AccountItemStatus(
                id="vehicle",
                verified=step_verified("vehicle") and step_verified("vehicle_number"),
            ),
            AccountItemStatus(
                id="license",
                verified=doc_verified(self.LICENSE_FRONT),
            ),
            AccountItemStatus(id="aadhaar", verified=doc_verified("AADHAAR")),
            AccountItemStatus(id="pan", verified=doc_verified("PAN")),
            AccountItemStatus(id="vehicle_rc", verified=doc_verified("VEHICLE_RC")),
            AccountItemStatus(id="bank", verified=bank_verified),
        ]

        return DriverRegistrationProgressResponse(
            kyc_status=driver.kyc_status,
            submitted=submitted,
            steps=steps,
            account_items=account_items,
        )

    async def get_saved_data(self, driver: Driver) -> DriverSavedRegistrationData:
        documents = await self._documents_for(driver.id)
        vehicle = await self._vehicle_for(driver.id)
        vehicle_type_id: str | None = None
        vehicle_type_name: str | None = None

        if vehicle:
            vehicle_type_id = str(vehicle.vehicle_type_id)
            vt_result = await self.db.execute(
                select(VehicleType).where(VehicleType.id == vehicle.vehicle_type_id)
            )
            vehicle_type = vt_result.scalar_one_or_none()
            vehicle_type_name = vehicle_type.name if vehicle_type else None

        doc_map: dict[str, SavedDocumentInfo] = {}
        for doc in documents:
            if not doc.document_url:
                continue
            doc_map[doc.document_type] = SavedDocumentInfo(
                url=doc.document_url,
                number=doc.document_number,
                status=doc.status or KYCStatus.PENDING.value,
            )

        license_number = (driver.license_number or "").strip()
        if license_number in ("", "PENDING"):
            license_number = None

        return DriverSavedRegistrationData(
            first_name=driver.first_name,
            last_name=driver.last_name or "",
            email=driver.email,
            phone=driver.phone,
            date_of_birth=driver.date_of_birth,
            gender=driver.gender,
            profile_photo=driver.profile_photo,
            city=driver.city,
            state=driver.state,
            country=driver.country,
            license_number=license_number,
            vehicle_number=vehicle.license_plate if vehicle else None,
            vehicle_type_id=vehicle_type_id,
            vehicle_type_name=vehicle_type_name,
            documents=doc_map,
        )

    async def _upsert_document(
        self,
        driver_id: UUID,
        document_type: str,
        document_url: str,
        document_number: str | None = None,
    ) -> DriverDocument:
        stored_url = persist_driver_image(
            document_url.strip(),
            str(driver_id),
            document_type.lower(),
        )
        if not stored_url:
            raise ValidationException("Could not save document image")

        result = await self.db.execute(
            select(DriverDocument).where(
                DriverDocument.driver_id == driver_id,
                DriverDocument.document_type == document_type,
            )
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.document_url = stored_url
            if document_number:
                doc.document_number = document_number
            doc.status = KYCStatus.PENDING.value
        else:
            doc = DriverDocument(
                driver_id=driver_id,
                document_type=document_type,
                document_url=stored_url,
                document_number=document_number,
                status=KYCStatus.PENDING.value,
            )
            self.db.add(doc)
        await self.db.flush()
        return doc

    async def save_license_upload(self, driver: Driver, data: SaveLicenseUpload) -> dict:
        doc_type = self.LICENSE_FRONT if data.side == "front" else self.LICENSE_BACK
        doc = await self._upsert_document(driver.id, doc_type, data.document_url)
        return {"id": str(doc.id), "side": data.side, "status": doc.status}

    async def save_license_number(self, driver: Driver, data: SaveLicenseNumber) -> dict:
        driver.license_number = data.license_number.strip().upper()
        await self.driver_repo.update(driver)
        return {"license_number": driver.license_number}

    async def save_profile(self, driver: Driver, data: SaveProfileStep) -> dict:
        driver.first_name = data.first_name.strip()
        driver.last_name = (data.last_name or "").strip()
        driver.date_of_birth = data.date_of_birth
        driver.gender = data.gender
        if data.city:
            driver.city = data.city.strip()
        if data.state:
            driver.state = data.state.strip()
        if data.country:
            driver.country = data.country.strip()
        if data.profile_photo:
            stored = persist_driver_image(data.profile_photo, str(driver.id), "selfie")
            if stored:
                driver.profile_photo = stored
        await self.driver_repo.update(driver)
        return {"message": "Profile saved"}

    async def save_vehicle_number(self, driver: Driver, data: SaveVehicleNumberStep) -> dict:
        vehicle = await self._vehicle_for(driver.id)
        if vehicle:
            vehicle.license_plate = data.license_plate
            if data.vehicle_type_id:
                vehicle.vehicle_type_id = data.vehicle_type_id
        else:
            if not data.vehicle_type_id:
                raise ValidationException("vehicle_type_id is required")
            vehicle = Vehicle(
                driver_id=driver.id,
                vehicle_type_id=data.vehicle_type_id,
                license_plate=data.license_plate,
                make="Standard",
                model="Standard",
                color="Unknown",
                year=datetime.now(timezone.utc).year,
            )
            self.db.add(vehicle)

        if data.rc_front_url:
            await self._upsert_document(driver.id, "VEHICLE_RC", data.rc_front_url)
        if data.rc_back_url:
            await self._upsert_document(driver.id, "VEHICLE_RC_BACK", data.rc_back_url)

        await self.db.flush()
        return {
            "vehicle_id": str(vehicle.id),
            "license_plate": vehicle.license_plate,
        }

    async def save_kyc(self, driver: Driver, data: SaveKycStep) -> dict:
        front_type = data.id_type
        back_type = f"{data.id_type}_BACK" if data.id_type == "AADHAAR" else None

        await self._upsert_document(
            driver.id,
            front_type,
            data.front_url,
            data.document_number.strip(),
        )
        if back_type and data.back_url:
            await self._upsert_document(driver.id, back_type, data.back_url)

        return {"id_type": data.id_type, "status": KYCStatus.PENDING.value}

    async def submit(self, driver: Driver) -> dict:
        documents = await self._documents_for(driver.id)
        vehicle = await self._vehicle_for(driver.id)

        license_front = self._doc_url(documents, self.LICENSE_FRONT)
        license_number = (driver.license_number or "").strip()
        if not license_front or license_number in ("", "PENDING"):
            raise ValidationException("Driving license upload and number are required")
        if not driver.profile_photo or not (driver.first_name or "").strip():
            raise ValidationException("Profile photo and name are required")
        if not vehicle or not (vehicle.license_plate or "").strip():
            raise ValidationException("Vehicle number is required")

        aadhaar = self._doc_url(documents, "AADHAAR")
        pan = self._doc_url(documents, "PAN")
        if not aadhaar and not pan:
            raise ValidationException("Aadhaar or PAN document is required")

        driver.kyc_status = KYCStatus.SUBMITTED.value
        await self.driver_repo.update(driver)
        return {
            "kyc_status": driver.kyc_status,
            "message": "Documents submitted for verification",
        }
