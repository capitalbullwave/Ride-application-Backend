"""Seed database with initial data."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import hash_password
from app.database.session import AsyncSessionLocal
from app.admin.models import AdminRolePermission
from app.core.constants import UserRole
from app.models import AdminPermission, AdminRole, AdminUser, City, User, VehicleType


async def seed():
    async with AsyncSessionLocal() as db:
        # Vehicle Types
        existing = await db.execute(select(VehicleType))
        if not existing.scalars().first():
            types = [
                VehicleType(name="Bike", slug="bike", description="Two-wheeler rides", base_fare=25, per_km_rate=8, per_minute_rate=1.5, capacity=1),
                VehicleType(name="Auto", slug="auto", description="Three-wheeler auto rickshaw", base_fare=30, per_km_rate=10, per_minute_rate=1.5, capacity=3),
                VehicleType(name="Economy", slug="economy", description="Affordable everyday rides", base_fare=40, per_km_rate=12, per_minute_rate=2, capacity=4),
                VehicleType(name="Comfort", slug="comfort", description="Extra legroom and comfort", base_fare=60, per_km_rate=16, per_minute_rate=2.5, capacity=4),
                VehicleType(name="Premium", slug="premium", description="Luxury vehicles", base_fare=100, per_km_rate=25, per_minute_rate=3, capacity=4),
                VehicleType(name="XL", slug="xl", description="6-seater for groups", base_fare=80, per_km_rate=18, per_minute_rate=2.5, capacity=6),
            ]
            db.add_all(types)

        # Admin Role & Permissions
        role_result = await db.execute(select(AdminRole).where(AdminRole.name == "Super Admin"))
        role = role_result.scalar_one_or_none()
        if not role:
            role = AdminRole(name="Super Admin", description="Full system access")
            db.add(role)
            await db.flush()

            permissions = [
                AdminPermission(name="Manage Users", codename="manage_users", module="users"),
                AdminPermission(name="Manage Drivers", codename="manage_drivers", module="drivers"),
                AdminPermission(name="Manage Rides", codename="manage_rides", module="rides"),
                AdminPermission(name="Manage Payments", codename="manage_payments", module="payments"),
                AdminPermission(name="View Analytics", codename="view_analytics", module="analytics"),
                AdminPermission(name="Manage Settings", codename="manage_settings", module="settings"),
            ]
            db.add_all(permissions)
            await db.flush()
            for perm in permissions:
                db.add(AdminRolePermission(role_id=role.id, permission_id=perm.id))

        # Admin User
        admin_result = await db.execute(select(AdminUser).where(AdminUser.email == "admin@ridebook.com"))
        if not admin_result.scalar_one_or_none():
            admin = AdminUser(
                email="admin@ridebook.com",
                password_hash=hash_password("Admin@123456"),
                first_name="Super",
                last_name="Admin",
                role_id=role.id,
            )
            db.add(admin)

        # Cities
        city_result = await db.execute(select(City))
        if not city_result.scalars().first():
            cities = [
                City(name="Mumbai", state="Maharashtra", lat=19.0760, lng=72.8777),
                City(name="Delhi", state="Delhi", lat=28.7041, lng=77.1025),
                City(name="Bangalore", state="Karnataka", lat=12.9716, lng=77.5946),
                City(name="Hyderabad", state="Telangana", lat=17.3850, lng=78.4867),
                City(name="Chennai", state="Tamil Nadu", lat=13.0827, lng=80.2707),
            ]
            db.add_all(cities)

        # Demo user for mobile OTP login testing
        user_result = await db.execute(select(User).where(User.phone == "+919876543210"))
        if not user_result.scalar_one_or_none():
            db.add(
                User(
                    email="user@ridebook.app",
                    phone="+919876543210",
                    password_hash=hash_password("User@123456"),
                    first_name="Demo",
                    last_name="User",
                    role=UserRole.USER.value,
                    is_verified=True,
                )
            )

        await db.commit()
        print("Database seeded successfully!")
        print("Admin login: admin@ridebook.com / Admin@123456")
        print("User OTP login phone: +919876543210 (must exist in Twilio verified numbers for trial)")


if __name__ == "__main__":
    asyncio.run(seed())
