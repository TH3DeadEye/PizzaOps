from typing import Any
from uuid import UUID

from fastapi.responses import JSONResponse

from services.inventory.models import Model


class ErrorResponse(Model):
    code: str
    message: str
    trace_id: UUID
    details: dict[str, Any]


class InventoryError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def response(self, trace_id):
        body = ErrorResponse(code=self.code, message=self.message, trace_id=trace_id, details=self.details)
        return JSONResponse(status_code=self.status, content=body.model_dump(mode="json", by_alias=True))
