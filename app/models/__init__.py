"""
Central model registry.

All ORM models are defined in domain modules and re-exported here so existing
``from app.models import User`` imports keep working and Alembic sees full metadata.
"""
from app.admin.models import (
    AdminLog,
    AdminPermission,
    AdminRole,
    AdminRolePermission,
    AdminUser,
)
from app.auth.models import AuthDevice, OtpLog, UserSession
from app.common.models import AuditLog
from app.coupons.models import PromoCode, ReferralCode, ReferralProgram, ReferralReward
from app.drivers.models import Driver, DriverBankAccount, DriverDocument, DriverEmergencyContact, DriverLocation
from app.notifications.models import Notification
from app.payments.models import Payment
from app.platform_settings.models import AppSetting, City, PricingRule, SystemConfig
from app.ratings.models import Rating
from app.rides.models import ChatMessage, Ride, RideEvent, RideTracking
from app.support.models import Faq, SupportTicket, SupportTicketReply
from app.subscriptions.models import StudentPass, SubscriptionPayment, SubscriptionPlan, UserSubscription
from app.users.models import SavedAddress, User
from app.vehicles.models import Vehicle, VehicleType
from app.commission.models import (
    CommissionSettings,
    CompanyRevenueLedger,
    DriverWallet,
    DriverWalletTransaction,
)
from app.wallet.models import Wallet, WalletTopUpPayment, WalletTransaction, WithdrawalRequest, UserBankAccount

__all__ = [
    "AdminLog",
    "AdminPermission",
    "AdminRole",
    "AdminRolePermission",
    "AdminUser",
    "AppSetting",
    "AuditLog",
    "AuthDevice",
    "ChatMessage",
    "City",
    "CommissionSettings",
    "CompanyRevenueLedger",
    "Driver",
    "DriverBankAccount",
    "DriverDocument",
    "DriverEmergencyContact",
    "DriverLocation",
    "DriverWallet",
    "DriverWalletTransaction",
    "Faq",
    "Notification",
    "OtpLog",
    "Payment",
    "PricingRule",
    "PromoCode",
    "Rating",
    "ReferralCode",
    "ReferralProgram",
    "ReferralReward",
    "Ride",
    "RideEvent",
    "RideTracking",
    "SavedAddress",
    "StudentPass",
    "SubscriptionPayment",
    "SubscriptionPlan",
    "SupportTicket",
    "SupportTicketReply",
    "SystemConfig",
    "User",
    "UserSubscription",
    "UserSession",
    "Vehicle",
    "VehicleType",
    "Wallet",
    "WalletTopUpPayment",
    "WalletTransaction",
    "WithdrawalRequest",
    "UserBankAccount",
]
