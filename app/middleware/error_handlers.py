"""Global exception handlers for FastAPI."""
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jose import JWTError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.exceptions import AppException
from app.core.logging import get_logger

logger = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "message": exc.message, "details": exc.details},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for err in exc.errors():
            errors.append({"field": ".".join(str(loc) for loc in err["loc"]), "message": err["msg"]})
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"success": False, "message": "Validation failed", "details": errors},
        )

    @app.exception_handler(JWTError)
    async def jwt_exception_handler(request: Request, exc: JWTError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"success": False, "message": "Invalid or expired token", "details": {}},
        )

    @app.exception_handler(IntegrityError)
    async def integrity_exception_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("integrity_error", error=str(exc.orig))
        err = str(exc.orig).lower()
        if "license_plate" in err:
            message = "This vehicle number is already registered"
        elif "drivers" in err and "phone" in err:
            message = "This phone number is already registered"
        elif "drivers" in err and "email" in err:
            message = "This email is already registered"
        elif "foreign key" in err or "violates" in err or "still referenced" in err:
            message = "Cannot delete this record because related data still exists"
        elif "users" in err and "phone" in err:
            message = "This phone number is already registered"
        elif "users" in err and "email" in err:
            message = "This email is already registered"
        else:
            message = "A record with this value already exists"
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"success": False, "message": message, "details": {}},
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Database error", "details": {}},
        )
