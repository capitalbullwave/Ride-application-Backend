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
    SaveVehicleDocumentsStep,
    SaveVehicleNumberStep,
    SaveVehicleTypeStep,
)
from app.services.driver_bank_service import DriverBankService
from app.services.image_storage import persist_driver_image
from app.services.vehicle_document_requirements import (
    document_label,
    missing_documents,
    required_document_types,
)
from app.vehicles.models import Vehicle, VehicleType


def _placeholder_license_plate(driver_id: UUID) -> str:
    """Per-driver placeholder until a real plate is saved (license_plate is globally unique)."""
    return f"PD{driver_id.hex[:18].upper()}"


def _is_placeholder_plate(plate: str | None) -> bool:
    if not plate:
        return True
    normalized = plate.strip().upper()
    if normalized in ("", "PENDING"):
        return True
    return len(normalized) == 20 and normalized.startswith("PD")


class DriverRegistrationProgressService:
    LICENSE_FRONT = "DRIVING_LICENSE"
    LICENSE_BACK = "DRIVING_LICENSE_BACK"

    VEHICLE_DOC_FIELDS = {
        "INSURANCE": "insurance_url",
        "POLLUTION": "pollution_url",
        "PERMIT": "permit_url",
        "FITNESS": "fitness_url",
        "VEHICLE_FRONT": "vehicle_front_url",
        "VEHICLE_BACK": "vehicle_back_url",
        "VEHICLE_SIDE": "vehicle_side_url",
    }

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

    async def _vehicle_type_name(self, vehicle: Vehicle | None) -> str | None:
        if not vehicle:
            return None
        vt_result = await self.db.execute(
            select(VehicleType).where(VehicleType.id == vehicle.vehicle_type_id)
        )
        vehicle_type = vt_result.scalar_one_or_none()
        return vehicle_type.name if vehicle_type else None

    def _doc_url(self, documents: list[DriverDocument], doc_type: str) -> str | None:
        for doc in documents:
            if doc.document_type == doc_type and doc.document_url:
                return doc.document_url
        return None

    def _doc_number(self, documents: list[DriverDocument], doc_type: str) -> str | None:
        for doc in documents:
            if doc.document_type == doc_type and doc.document_number:
                return doc.document_number
        return None

    def _doc_status(self, documents: list[DriverDocument], doc_type: str) -> str:
        for doc in documents:
            if doc.document_type == doc_type:
                return doc.status or KYCStatus.PENDING.value
        return "pending"

    def _uploaded_types(self, documents: list[DriverDocument]) -> set[str]:
        return {
            doc.document_type
            for doc in documents
            if doc.document_url and doc.document_type
        }

    def _vehicle_type_selected(self, vehicle: Vehicle | None) -> bool:
        return vehicle is not None and vehicle.vehicle_type_id is not None

    def _license_done(
        self, documents: list[DriverDocument], driver: Driver
    ) -> bool:
        license_front = self._doc_url(documents, self.LICENSE_FRONT)
        license_number = (driver.license_number or "").strip()
        return bool(license_front) and license_number not in ("", "PENDING")

    def _profile_done(self, driver: Driver) -> bool:
        return (
            bool((driver.first_name or "").strip())
            and bool(driver.profile_photo)
            and driver.date_of_birth is not None
            and bool((driver.gender or "").strip())
        )

    def _vehicle_number_done(
        self, documents: list[DriverDocument], vehicle: Vehicle | None
    ) -> bool:
        if not vehicle:
            return False
        plate = (vehicle.license_plate or "").strip()
        if _is_placeholder_plate(plate):
            return False
        rc_front = self._doc_url(documents, "VEHICLE_RC")
        rc_back = self._doc_url(documents, "VEHICLE_RC_BACK")
        return bool(rc_front) and bool(rc_back)

    def _kyc_done(self, documents: list[DriverDocument]) -> bool:
        aadhaar_front = self._doc_url(documents, "AADHAAR")
        aadhaar_back = self._doc_url(documents, "AADHAAR_BACK")
        aadhaar_number = self._doc_number(documents, "AADHAAR")
        return bool(aadhaar_front) and bool(aadhaar_back) and bool(aadhaar_number)

    def _vehicle_docs_done(
        self, documents: list[DriverDocument], vehicle_type_name: str | None
    ) -> bool:
        uploaded = self._uploaded_types(documents)
        return len(missing_documents(vehicle_type_name, uploaded)) == 0

    async def get_progress(self, driver: Driver) -> DriverRegistrationProgressResponse:
        documents = await self._documents_for(driver.id)
        vehicle = await self._vehicle_for(driver.id)
        vehicle_type_name = await self._vehicle_type_name(vehicle)

        vehicle_done = self._vehicle_type_selected(vehicle)
        license_done = self._license_done(documents, driver)
        profile_done = self._profile_done(driver)
        vehicle_number_done = self._vehicle_number_done(documents, vehicle)
        kyc_done = self._kyc_done(documents)
        vehicle_docs_done = self._vehicle_docs_done(documents, vehicle_type_name)

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
                completed=vehicle_done,
                status=step_status(vehicle_done),
                subtitle=vehicle_type_name if vehicle_done else None,
            ),
            RegistrationStepInfo(
                id="photo_name",
                completed=profile_done,
                status=step_status(profile_done),
            ),
            RegistrationStepInfo(
                id="license",
                completed=license_done,
                status=step_status(
                    license_done,
                    [self.LICENSE_FRONT],
                ),
            ),
            RegistrationStepInfo(
                id="vehicle_number",
                completed=vehicle_number_done,
                status=step_status(
                    vehicle_number_done,
                    ["VEHICLE_RC", "VEHICLE_RC_BACK"],
                ),
            ),
            RegistrationStepInfo(
                id="kyc",
                completed=kyc_done,
                status=step_status(kyc_done, ["AADHAAR", "AADHAAR_BACK"]),
            ),
            RegistrationStepInfo(
                id="vehicle_docs",
                completed=vehicle_docs_done,
                status=step_status(
                    vehicle_docs_done,
                    required_document_types(vehicle_type_name),
                ),
                subtitle=vehicle_type_name,
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
            vehicle_type_name = await self._vehicle_type_name(vehicle)

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

        vehicle_number = None
        if vehicle:
            plate = (vehicle.license_plate or "").strip()
            if not _is_placeholder_plate(plate):
                vehicle_number = plate

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
            vehicle_number=vehicle_number,
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
        if not data.date_of_birth:
            raise ValidationException("Date of birth is required")
        if not (data.gender or "").strip():
            raise ValidationException("Gender is required")
        if not data.profile_photo:
            raise ValidationException("Profile photo is required")

        driver.first_name = data.first_name.strip()
        driver.last_name = (data.last_name or "").strip()
        driver.date_of_birth = data.date_of_birth
        driver.gender = data.gender.strip()
        if data.city:
            driver.city = data.city.strip()
        if data.state:
            driver.state = data.state.strip()
        if data.country:
            driver.country = data.country.strip()
        stored = persist_driver_image(data.profile_photo, str(driver.id), "selfie")
        if stored:
            driver.profile_photo = stored
        await self.driver_repo.update(driver)
        return {"message": "Profile saved", "profile_photo": driver.profile_photo}

    async def save_vehicle_type(self, driver: Driver, data: SaveVehicleTypeStep) -> dict:
        vehicle = await self._vehicle_for(driver.id)
        if vehicle:
            vehicle.vehicle_type_id = data.vehicle_type_id
        else:
            vehicle = Vehicle(
                driver_id=driver.id,
                vehicle_type_id=data.vehicle_type_id,
                license_plate=_placeholder_license_plate(driver.id),
                make="Standard",
                model="Standard",
                color="Unknown",
                year=datetime.now(timezone.utc).year,
            )
            self.db.add(vehicle)
        await self.db.flush()
        return {
            "vehicle_id": str(vehicle.id),
            "vehicle_type_id": str(vehicle.vehicle_type_id),
        }

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

        if not data.rc_front_url:
            raise ValidationException("RC front image is required")
        if not data.rc_back_url:
            raise ValidationException("RC back image is required")

        await self._upsert_document(driver.id, "VEHICLE_RC", data.rc_front_url)
        await self._upsert_document(driver.id, "VEHICLE_RC_BACK", data.rc_back_url)

        await self.db.flush()
        return {
            "vehicle_id": str(vehicle.id),
            "license_plate": vehicle.license_plate,
        }

    async def save_vehicle_documents(
        self, driver: Driver, data: SaveVehicleDocumentsStep
    ) -> dict:
        vehicle = await self._vehicle_for(driver.id)
        if not vehicle:
            raise ValidationException("Select vehicle type before uploading documents")

        vehicle_type_name = await self._vehicle_type_name(vehicle)
        saved: list[str] = []

        for doc_type, field_name in self.VEHICLE_DOC_FIELDS.items():
            url = getattr(data, field_name, None)
            if url:
                await self._upsert_document(driver.id, doc_type, url)
                saved.append(doc_type)

        uploaded = self._uploaded_types(await self._documents_for(driver.id))
        missing = missing_documents(vehicle_type_name, uploaded)

        return {
            "saved": saved,
            "vehicle_type": vehicle_type_name,
            "complete": len(missing) == 0,
            "missing": missing,
        }

    async def save_kyc(self, driver: Driver, data: SaveKycStep) -> dict:
        if data.id_type == "AADHAAR" and not data.back_url:
            raise ValidationException("Aadhaar back image is required")

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
        vehicle_type_name = await self._vehicle_type_name(vehicle)

        if not self._vehicle_type_selected(vehicle):
            raise ValidationException("Vehicle type is required")
        if not self._profile_done(driver):
            raise ValidationException(
                "Profile photo, name, date of birth and gender are required"
            )
        if not self._license_done(documents, driver):
            raise ValidationException(
                "Driving license front and number are required"
            )
        if not self._vehicle_number_done(documents, vehicle):
            raise ValidationException(
                "Vehicle number with RC front and back is required"
            )
        if not self._kyc_done(documents):
            raise ValidationException(
                "Aadhaar front, back and number are required"
            )
        if not self._vehicle_docs_done(documents, vehicle_type_name):
            missing = missing_documents(
                vehicle_type_name, self._uploaded_types(documents)
            )
            labels = ", ".join(document_label(t) for t in missing)
            raise ValidationException(
                f"Upload all required vehicle documents: {labels}"
            )

        driver.kyc_status = KYCStatus.SUBMITTED.value
        await self.driver_repo.update(driver)
        return {
            "kyc_status": driver.kyc_status,
            "message": "Documents submitted for verification",
        }
