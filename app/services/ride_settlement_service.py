"""Atomic ride settlement on completion — commission, wallet, revenue ledger."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commission.models import CompanyRevenueLedger
from app.core.constants import RideStatus
from app.core.exceptions import ValidationException
from app.models import Ride
from app.services.commission_service import CommissionService
from app.services.driver_wallet_service import DriverWalletService
from app.notifications.service import NotificationService

logger = logging.getLogger(__name__)


def compute_ride_split(fare: float, driver_commission_pct: float) -> tuple[float, float]:
    """Split fare into driver earnings and company commission (platform cut).

    ``driver_commission_pct`` is the driver's share of the fare (0–100).
    """
    fare = round(float(fare or 0), 2)
    pct = max(0.0, min(100.0, float(driver_commission_pct)))
    driver_earning = round(fare * pct / 100.0, 2)
    company_earning = round(fare - driver_earning, 2)
    return driver_earning, company_earning


class RideSettlementService:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db
    self.commission = CommissionService(db)
    self.driver_wallet = DriverWalletService(db)

  async def settle_completed_ride(self, ride: Ride) -> Ride:
    if ride.status != RideStatus.COMPLETED.value:
      return ride

    if ride.driver_id is None:
      return ride

    # Already settled with real split values on the ride row.
    if ride.driver_earning is not None and ride.company_earning is not None:
      return ride

    locked = await self.db.execute(
      select(Ride).where(Ride.id == ride.id).with_for_update()
    )
    ride = locked.scalar_one()

    if ride.driver_earning is not None and ride.company_earning is not None:
      return ride

    fare = float(ride.final_fare or ride.estimated_fare or 0)
    if fare < 0:
      raise ValidationException("Ride fare cannot be negative")

    commission_pct = await self.commission.get_percentage_for_vehicle_type_id(
      ride.vehicle_type_id
    )
    driver_earning, company_earning = compute_ride_split(fare, commission_pct)

    ride.driver_commission_percentage = commission_pct
    ride.driver_earning = driver_earning
    ride.company_earning = company_earning

    already_credited = await self.driver_wallet.has_ride_credit(ride.id)
    if driver_earning > 0 and not already_credited:
      await self.driver_wallet.credit_ride_earning(
        driver_id=ride.driver_id,
        ride_id=ride.id,
        amount=driver_earning,
        description="Ride Completed",
      )

    if company_earning > 0:
      existing_ledger = await self.db.execute(
        select(CompanyRevenueLedger.id)
        .where(CompanyRevenueLedger.ride_id == ride.id)
        .limit(1)
      )
      if existing_ledger.scalar_one_or_none() is None:
        ledger = CompanyRevenueLedger(
          ride_id=ride.id,
          amount=company_earning,
          description=f"Company revenue from ride {str(ride.id)[:8]}",
        )
        self.db.add(ledger)

    await self.db.flush()

    try:
      from app.services.referral_service import ReferralService

      await ReferralService(self.db).process_after_ride_completed(ride)
    except Exception:
      logger.exception("Referral processing failed for ride %s", ride.id)

    if driver_earning > 0 and not already_credited:
      try:
        notif = NotificationService(self.db)
        await notif.create_in_app(
          title="Ride earnings credited",
          message=f"₹{driver_earning:.2f} added to your wallet.",
          notification_type="PAYMENT",
          driver_id=ride.driver_id,
          data={"ride_id": str(ride.id), "amount": driver_earning},
        )
        await notif.create_in_app(
          title="Rate your driver",
          message="How was your trip? Tap to rate your captain.",
          notification_type="RIDE",
          user_id=ride.user_id,
          data={"ride_id": str(ride.id), "event": "rate_driver"},
        )
      except Exception:
        logger.exception("Failed to send settlement notifications for ride %s", ride.id)

    return ride
