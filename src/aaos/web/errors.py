"""Safe browser error handling and request correlation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from aaos.web.contracts import ApiErrorDetail, ApiErrorResponse

_REQUEST_ID_HEADER = "X-Request-ID"


class Phase7ApiError(Exception):
    """Expected browser-safe Phase 7 API failure."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def request_id_from(request: Request) -> str:
    """Return the application-owned request correlation identifier."""

    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        return request_id

    request_id = uuid4().hex
    request.state.request_id = request_id
    return request_id


def _safe_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    request_id = request_id_from(request)
    payload = ApiErrorResponse(
        error=ApiErrorDetail(
            code=code,
            message=message,
            request_id=request_id,
        )
    )

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={_REQUEST_ID_HEADER: request_id},
    )


def install_error_boundary(application: FastAPI) -> None:
    """Install request correlation and closed browser-safe error handling."""

    @application.middleware("http")
    async def _request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id_from(request)
        return response

    @application.exception_handler(Phase7ApiError)
    async def _handle_phase7_api_error(
        request: Request,
        error: Phase7ApiError,
    ) -> JSONResponse:
        return _safe_error_response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.message,
        )

    @application.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return _safe_error_response(
            request,
            status_code=422,
            code="INVALID_REQUEST",
            message="The request is invalid.",
        )

    @application.exception_handler(Exception)
    async def _handle_unexpected_error(
        request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return _safe_error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="AAOS could not complete the request.",
        )
