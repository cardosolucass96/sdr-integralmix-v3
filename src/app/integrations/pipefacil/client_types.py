from dataclasses import dataclass
from typing import Any

from app.integrations.pipefacil.contracts import PipefacilDeliveryErrorCode


class PipefacilSendMessageError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: PipefacilDeliveryErrorCode,
        status_code: int | None = None,
        request_id: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body


class PipefacilMediaDownloadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        content_length: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.content_length = content_length


class PipefacilDealLookupError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        request_id: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body


class PipefacilConversationHistoryLookupError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        request_id: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body


class PipefacilDealUpdateError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        request_id: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        self.response_body = response_body


@dataclass(slots=True)
class PipefacilSendMessageResult:
    status_code: int
    request_id: str | None
    payload: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class PipefacilDownloadedMedia:
    content: bytes
    content_type: str | None
    content_length: int
    status_code: int


@dataclass(frozen=True, slots=True)
class PipefacilDealUpdateResult:
    status_code: int
    request_id: str | None
    payload: dict[str, Any] | None
