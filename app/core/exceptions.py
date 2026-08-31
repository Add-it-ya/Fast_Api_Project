import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors that are safe to show to a client."""

    status_code = 500
    detail = 'Internal server error'

    def __init__(self, detail: str | None = None):
        self.detail = detail or self.detail
        super().__init__(self.detail)


class InvalidCredentialsError(AppError):
    status_code = 401
    detail = 'Invalid username or password'


class InvalidTokenError(AppError):
    status_code = 401
    detail = 'Invalid or expired token'


class InvalidApiKeyError(AppError):
    status_code = 401
    detail = 'Missing or invalid API key'


class UserAlreadyExistsError(AppError):
    status_code = 409
    detail = 'Username is already taken'


class RateLimitExceededError(AppError):
    status_code = 429
    detail = 'Too many requests'


class ModelUnavailableError(AppError):
    status_code = 503
    detail = 'Prediction model is not available'


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Log the real error, return a generic one. Echoing str(exc) to the
        # client leaks connection strings, file paths and query fragments.
        logger.exception('Unhandled error on %s %s', request.method, request.url.path)
        return JSONResponse(status_code=500, content={'detail': 'Internal server error'})
