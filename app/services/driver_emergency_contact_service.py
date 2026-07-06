"""Driver emergency contact CRUD."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.drivers.models import DriverEmergencyContact
from app.schemas.driver import EmergencyContactCreate, EmergencyContactResponse, EmergencyContactUpdate


def contact_to_response(contact: DriverEmergencyContact) -> EmergencyContactResponse:
    return EmergencyContactResponse(
        id=contact.id,
        name=contact.name,
        phone=contact.phone,
        relation=contact.relation,
    )


class DriverEmergencyContactService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_for_driver(self, driver_id: uuid.UUID) -> list[DriverEmergencyContact]:
        result = await self.db.execute(
            select(DriverEmergencyContact)
            .where(DriverEmergencyContact.driver_id == driver_id)
            .order_by(DriverEmergencyContact.created_at.asc())
        )
        return list(result.scalars().all())

    async def create(self, driver_id: uuid.UUID, data: EmergencyContactCreate) -> DriverEmergencyContact:
        contact = DriverEmergencyContact(
            driver_id=driver_id,
            name=data.name.strip(),
            phone=data.phone.strip(),
            relation=data.relation.strip() if data.relation else None,
        )
        self.db.add(contact)
        await self.db.flush()
        await self.db.refresh(contact)
        return contact

    async def update(
        self,
        driver_id: uuid.UUID,
        contact_id: uuid.UUID,
        data: EmergencyContactUpdate,
    ) -> DriverEmergencyContact:
        contact = await self._get_owned(driver_id, contact_id)
        if data.name is not None:
            contact.name = data.name.strip()
        if data.phone is not None:
            contact.phone = data.phone.strip()
        if data.relation is not None:
            contact.relation = data.relation.strip() or None
        await self.db.flush()
        await self.db.refresh(contact)
        return contact

    async def delete(self, driver_id: uuid.UUID, contact_id: uuid.UUID) -> None:
        contact = await self._get_owned(driver_id, contact_id)
        await self.db.delete(contact)
        await self.db.flush()

    async def _get_owned(self, driver_id: uuid.UUID, contact_id: uuid.UUID) -> DriverEmergencyContact:
        result = await self.db.execute(
            select(DriverEmergencyContact).where(
                DriverEmergencyContact.id == contact_id,
                DriverEmergencyContact.driver_id == driver_id,
            )
        )
        contact = result.scalar_one_or_none()
        if not contact:
            raise NotFoundException("Emergency contact not found")
        return contact
