from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

PROTOCOL_VERSION = "1.0.0"
ENGINE_NAME = "pkulaw-engine"


class ProtocolValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def event_message(event: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "event",
        "event": event,
        "protocolVersion": PROTOCOL_VERSION,
        "timestamp": utc_now(),
        "data": data or {},
    }


def response_message(
    request_id: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "response",
        "requestId": request_id,
        "protocolVersion": PROTOCOL_VERSION,
        "timestamp": utc_now(),
        "ok": error is None,
    }
    if error is None:
        message["result"] = result or {}
    else:
        message["error"] = error
    return message


def validate_request(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ProtocolValidationError("INVALID_MESSAGE", "message must be an object")
    if message.get("type") != "request":
        raise ProtocolValidationError("UNSUPPORTED_MESSAGE_TYPE", "expected request message")

    request_id = message.get("requestId")
    method = message.get("method")
    params = message.get("params", {})

    if not isinstance(request_id, str) or not request_id.strip():
        raise ProtocolValidationError("INVALID_REQUEST_ID", "requestId must be a non-empty string")
    if not isinstance(method, str) or not method.strip():
        raise ProtocolValidationError("INVALID_METHOD", "method must be a non-empty string")
    if not isinstance(params, dict):
        raise ProtocolValidationError("INVALID_PARAMS", "params must be an object")

    return {
        "requestId": request_id,
        "method": method,
        "params": params,
    }
