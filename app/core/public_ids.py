"""Human-readable Bull Wave Rides public IDs (BWR-U/D/R-XXXXXX)."""
from sqlalchemy import event, text

from app.drivers.models import Driver
from app.rides.models import Ride
from app.users.models import User

USER_PREFIX = "BWR-U"
DRIVER_PREFIX = "BWR-D"
RIDE_PREFIX = "BWR-R"

USER_SEQUENCE = "user_public_id_seq"
DRIVER_SEQUENCE = "driver_public_id_seq"
RIDE_SEQUENCE = "ride_public_id_seq"


def format_public_id(prefix: str, number: int) -> str:
    return f"{prefix}-{number:06d}"


def _assign_public_id(connection, *, prefix: str, sequence: str, target) -> None:
    if target.public_id:
        return
    number = connection.execute(text(f"SELECT nextval('{sequence}')")).scalar_one()
    target.public_id = format_public_id(prefix, int(number))


@event.listens_for(User, "before_insert")
def _user_public_id(_mapper, connection, target) -> None:
    _assign_public_id(connection, prefix=USER_PREFIX, sequence=USER_SEQUENCE, target=target)


@event.listens_for(Driver, "before_insert")
def _driver_public_id(_mapper, connection, target) -> None:
    _assign_public_id(connection, prefix=DRIVER_PREFIX, sequence=DRIVER_SEQUENCE, target=target)


@event.listens_for(Ride, "before_insert")
def _ride_public_id(_mapper, connection, target) -> None:
    _assign_public_id(connection, prefix=RIDE_PREFIX, sequence=RIDE_SEQUENCE, target=target)
