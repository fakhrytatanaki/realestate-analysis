"""Domain-error to HTTP mapping.

Keeps status codes out of the domain: services raise meaningful exceptions and
this module decides how they surface over HTTP.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from realestate.domain.exceptions import (
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    DataSourceDisabledError,
    DataSourceNotImplementedError,
    FetchError,
    ForbiddenError,
    InvalidQueryError,
    NotFoundError,
    ParseError,
    RealEstateError,
)

#: Most specific first -- subclasses must be listed before their bases, since
#: Starlette resolves handlers by walking the exception's MRO.
_STATUS_BY_EXCEPTION: tuple[tuple[type[RealEstateError], int], ...] = (
    (DataSourceNotImplementedError, status.HTTP_501_NOT_IMPLEMENTED),
    (DataSourceDisabledError, status.HTTP_409_CONFLICT),
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (InvalidQueryError, status.HTTP_400_BAD_REQUEST),
    (AuthorizationError, status.HTTP_401_UNAUTHORIZED),
    (ForbiddenError, status.HTTP_403_FORBIDDEN),
    (ConflictError, status.HTTP_409_CONFLICT),
    (ConfigurationError, status.HTTP_500_INTERNAL_SERVER_ERROR),
    (FetchError, status.HTTP_502_BAD_GATEWAY),
    (ParseError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (RealEstateError, status.HTTP_500_INTERNAL_SERVER_ERROR),
)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach a JSON handler for every domain error."""

    def make_handler(status_code: int):  # type: ignore[no-untyped-def]
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(
                status_code=status_code,
                content={"error": type(exc).__name__, "detail": str(exc)},
                # RFC 9110 requires a challenge on every 401.
                headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
            )

        return handler

    for exception_type, status_code in _STATUS_BY_EXCEPTION:
        app.add_exception_handler(exception_type, make_handler(status_code))
