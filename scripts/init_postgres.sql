-- Creates app database user and database for Ride Booking backend.
-- Run as PostgreSQL superuser (usually `postgres`).

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rideuser') THEN
        CREATE ROLE rideuser WITH LOGIN PASSWORD 'ridepass';
    ELSE
        ALTER ROLE rideuser WITH LOGIN PASSWORD 'ridepass';
    END IF;
END
$$;

SELECT 'CREATE DATABASE ridebooking OWNER rideuser'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ridebooking')\gexec

GRANT ALL PRIVILEGES ON DATABASE ridebooking TO rideuser;
