from enum import Enum


class UserRole(str, Enum):
    USER = "USER"
    DRIVER = "DRIVER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class RideStatus(str, Enum):
    REQUESTED = "REQUESTED"
    SEARCHING_DRIVER = "SEARCHING_DRIVER"
    DRIVER_ASSIGNED = "DRIVER_ASSIGNED"
    DRIVER_ARRIVED = "DRIVER_ARRIVED"
    OTP_VERIFIED = "OTP_VERIFIED"
    STARTED = "STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    # Legacy aliases (pre-unified backend)
    SEARCHING = "SEARCHING_DRIVER"
    ACCEPTED = "DRIVER_ASSIGNED"


ACTIVE_RIDE_STATUSES = frozenset({
    RideStatus.REQUESTED.value,
    RideStatus.SEARCHING_DRIVER.value,
    RideStatus.DRIVER_ASSIGNED.value,
    RideStatus.DRIVER_ARRIVED.value,
    RideStatus.OTP_VERIFIED.value,
    RideStatus.STARTED.value,
    RideStatus.IN_PROGRESS.value,
})

DRIVER_ACTIVE_RIDE_STATUSES = frozenset({
    RideStatus.DRIVER_ASSIGNED.value,
    RideStatus.DRIVER_ARRIVED.value,
    RideStatus.OTP_VERIFIED.value,
    RideStatus.STARTED.value,
    RideStatus.IN_PROGRESS.value,
})


class PaymentMethod(str, Enum):
    CASH = "CASH"
    WALLET = "WALLET"
    UPI = "UPI"
    CARD = "CARD"
    STRIPE = "STRIPE"
    RAZORPAY = "RAZORPAY"
    CASHFREE = "CASHFREE"
    PHONEPE = "PHONEPE"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class WalletTransactionType(str, Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    REFUND = "REFUND"
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"


class DriverWalletTransactionType(str, Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    WITHDRAWAL = "WITHDRAWAL"
    ADJUSTMENT = "ADJUSTMENT"
    BONUS = "BONUS"
    PENALTY = "PENALTY"


class DriverStatus(str, Enum):
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    ON_RIDE = "ON_RIDE"
    BUSY = "BUSY"


class KYCStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class VehicleStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DocumentType(str, Enum):
    DRIVING_LICENSE = "DRIVING_LICENSE"
    AADHAR = "AADHAR"
    PAN = "PAN"
    VEHICLE_RC = "VEHICLE_RC"
    INSURANCE = "INSURANCE"
    PROFILE_PHOTO = "PROFILE_PHOTO"


class NotificationType(str, Enum):
    RIDE = "RIDE"
    PAYMENT = "PAYMENT"
    PROMO = "PROMO"
    SYSTEM = "SYSTEM"
    CHAT = "CHAT"
    ADMIN = "ADMIN"


class SupportTicketStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class SupportTicketPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class WithdrawalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"


class RideEventType(str, Enum):
    REQUESTED = "REQUESTED"
    SEARCHING_DRIVER = "SEARCHING_DRIVER"
    DRIVER_ASSIGNED = "DRIVER_ASSIGNED"
    DRIVER_ARRIVED = "DRIVER_ARRIVED"
    OTP_VERIFIED = "OTP_VERIFIED"
    STARTED = "STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    PAYMENT = "PAYMENT"
    # Legacy
    SEARCHING = "SEARCHING_DRIVER"
    ACCEPTED = "DRIVER_ASSIGNED"


class ActorType(str, Enum):
    USER = "USER"
    DRIVER = "DRIVER"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"


class DevicePlatform(str, Enum):
    IOS = "IOS"
    ANDROID = "ANDROID"
    WEB = "WEB"


class RaterType(str, Enum):
    USER = "USER"
    DRIVER = "DRIVER"
