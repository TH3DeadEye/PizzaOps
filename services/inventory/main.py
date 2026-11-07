from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from services.inventory.errors import ErrorResponse, InventoryError
from services.inventory.events import EventPage, LOGGER, configure_logging
from services.inventory.models import (
    FaultRequest, FaultResponse, HealthResponse, Reservation, ReservationRequest,
    RestockRequest, RestockResponse, Snapshot, StockResponse,
)
from services.inventory.settings import Settings
from services.inventory.store import InventoryStore
from services.inventory.trace import read_trace


async def require_run(request: Request, run_id: Annotated[UUID, Header(alias="X-Run-Id")],
                      trace_id: Annotated[UUID | None, Header(alias="X-Trace-Id")] = None):
    store = request.state.inventory
    if run_id != store.settings.run_id:
        raise InventoryError(409, "RUN_MISMATCH", "Request belongs to a different simulation run",
                             {"runId": str(store.settings.run_id)})
    request.state.trace_id = trace_id or request.state.trace_id
    return store


Store = Annotated[InventoryStore, Depends(require_run)]


def create_app(settings=None, *, store_factory=InventoryStore):
    settings = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        configure_logging()
        store = store_factory(settings)
        application.state.inventory = store
        await store.lifecycle(starting=True)
        try:
            yield
        finally:
            await store.lifecycle(starting=False)

    application = FastAPI(title="PizzaOps Inventory", version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def correlation(request, call_next):
        request.state.inventory = request.app.state.inventory
        request.state.trace_id = uuid4()
        try:
            request.state.trace_id = read_trace(request.headers.get("X-Trace-Id"))
            response = await call_next(request)
        except InventoryError as error:
            response = error.response(request.state.trace_id)
        except Exception:
            LOGGER.exception("Unhandled inventory error; traceId=%s", request.state.trace_id)
            response = InventoryError(500, "INTERNAL_ERROR", "Inventory could not complete the request").response(request.state.trace_id)
        response.headers["X-Trace-Id"] = str(request.state.trace_id)
        response.headers["X-Run-Id"] = str(settings.run_id)
        response.headers["X-Instance-Id"] = str(request.state.inventory.instance_id)
        return response

    @application.exception_handler(InventoryError)
    async def domain_error(request, error):
        return error.response(request.state.trace_id)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        fields = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"], "type": item["type"]}
                  for item in error.errors()]
        return InventoryError(400, "INVALID_REQUEST", "Request validation failed", {"fields": fields}).response(request.state.trace_id)

    @application.exception_handler(HTTPException)
    async def http_error(request, error):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(error.status_code, "HTTP_ERROR")
        response = InventoryError(error.status_code, code, str(error.detail)).response(request.state.trace_id)
        for name, value in (error.headers or {}).items():
            response.headers[name] = value
        return response

    @application.get("/health", response_model=HealthResponse)
    async def health(request: Request):
        store = request.state.inventory
        return HealthResponse(run_id=settings.run_id, instance_id=store.instance_id)

    router = APIRouter(responses={status: {"model": ErrorResponse} for status in (400, 404, 409, 410, 503)})

    @router.get("/stock", response_model=StockResponse)
    async def stock(request: Request, store: Store,
                    ingredient: Annotated[str | None, Query(min_length=1, max_length=32, pattern=r"^[A-Z][A-Z_]*$")] = None,
                    authoritative: bool = False):
        return await store.stock(request.state.trace_id, ingredient, authoritative)

    @router.post("/reservations", response_model=Reservation)
    async def reserve(body: ReservationRequest, request: Request, store: Store):
        return await store.reserve(body, request.state.trace_id)

    @router.get("/reservations/{order_id}", response_model=Reservation)
    async def reservation(order_id: UUID, store: Store):
        return await store.reservation(order_id)

    @router.post("/admin/restock", response_model=RestockResponse)
    async def restock(body: RestockRequest, request: Request, store: Store):
        return await store.restock(body, request.state.trace_id)

    @router.post("/admin/faults", response_model=FaultResponse)
    async def set_faults(body: FaultRequest, request: Request, store: Store):
        return await store.set_faults(body, request.state.trace_id)

    @router.get("/snapshot", response_model=Snapshot)
    async def snapshot(store: Store):
        return await store.snapshot()

    @router.get("/events", response_model=EventPage)
    async def events(store: Store, instance_id: Annotated[UUID, Query(alias="instanceId")],
                     after: int = 0, limit: Annotated[int, Query(ge=1, le=500)] = 100):
        return await store.events(instance_id, after, limit)

    application.include_router(router)
    default_openapi = application.openapi

    def inventory_openapi():
        schema = default_openapi()
        for path in schema["paths"].values():
            for operation in path.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        return schema

    application.openapi = inventory_openapi
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8082, workers=1)
