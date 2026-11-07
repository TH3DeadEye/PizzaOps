from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from services.order.errors import ErrorResponse, OrderError
from services.order.events import EventPage, LOGGER, configure_logging
from services.order.kitchen_client import KitchenClient
from services.order.models import HealthResponse, Order, OrderPage, OrderRequest, Snapshot
from services.order.orders import OrderBook
from services.order.settings import Settings
from services.order.trace import read_trace
from services.order.workers import Fulfillment


async def require_run(request: Request, run_id: Annotated[UUID, Header(alias="X-Run-Id")],
                      trace_id: Annotated[UUID | None, Header(alias="X-Trace-Id")] = None):
    service = request.state.orders
    if run_id != service.settings.run_id:
        raise OrderError(409, "RUN_MISMATCH", "Request belongs to a different simulation run",
                         {"runId": str(service.settings.run_id)})
    request.state.trace_id = trace_id or request.state.trace_id
    return service


Service = Annotated[Fulfillment, Depends(require_run)]


def default_service(settings):
    return Fulfillment(settings, OrderBook(settings), KitchenClient(settings))


def create_app(settings=None, *, service_factory=default_service):
    settings = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        configure_logging()
        service = service_factory(settings)
        application.state.orders = service
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    application = FastAPI(title="PizzaOps Order", version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def correlation(request, call_next):
        request.state.orders = request.app.state.orders
        request.state.trace_id = uuid4()
        try:
            request.state.trace_id = read_trace(request.headers.get("X-Trace-Id"))
            response = await call_next(request)
        except OrderError as error:
            response = error.response(request.state.trace_id)
        except Exception:
            LOGGER.exception("Unhandled order error; traceId=%s", request.state.trace_id)
            response = OrderError(500, "INTERNAL_ERROR", "Order could not complete the request").response(request.state.trace_id)
        response.headers["X-Trace-Id"] = str(request.state.trace_id)
        response.headers["X-Run-Id"] = str(settings.run_id)
        response.headers["X-Instance-Id"] = str(request.state.orders.instance_id)
        return response

    @application.exception_handler(OrderError)
    async def domain_error(request, error):
        return error.response(request.state.trace_id)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        fields = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"], "type": item["type"]}
                  for item in error.errors()]
        return OrderError(400, "INVALID_REQUEST", "Request validation failed", {"fields": fields}).response(request.state.trace_id)

    @application.exception_handler(HTTPException)
    async def http_error(request, error):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(error.status_code, "HTTP_ERROR")
        response = OrderError(error.status_code, code, str(error.detail)).response(request.state.trace_id)
        for name, value in (error.headers or {}).items():
            response.headers[name] = value
        return response

    @application.get("/health", response_model=HealthResponse)
    async def health(request: Request):
        return HealthResponse(run_id=settings.run_id, instance_id=request.state.orders.instance_id)

    router = APIRouter(responses={status: {"model": ErrorResponse} for status in (400, 404, 409, 410, 503)})

    @router.post("/orders", response_model=Order, status_code=201, responses={200: {"model": Order, "description": "Existing order"}})
    async def place_order(body: OrderRequest, request: Request, response: Response, service: Service):
        order, created = await service.book.submit(body, request.state.trace_id)
        response.status_code = 201 if created else 200
        response.headers["Location"] = f"/orders/{order.order_id}"
        return order

    @router.get("/orders/{order_id}", response_model=Order)
    async def order_status(order_id: UUID, service: Service):
        return await service.book.order(order_id)

    @router.get("/orders", response_model=OrderPage)
    async def list_orders(service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                          cursor: Annotated[str | None, Query(min_length=1, max_length=64)] = None):
        return await service.book.list(limit, cursor)

    @router.get("/snapshot", response_model=Snapshot)
    async def snapshot(service: Service):
        return await service.snapshot()

    @router.get("/events", response_model=EventPage)
    async def events(service: Service, instance_id: Annotated[UUID, Query(alias="instanceId")],
                     after: int = 0, limit: Annotated[int, Query(ge=1, le=500)] = 100):
        return await service.book.events(instance_id, after, limit)

    application.include_router(router)
    default_openapi = application.openapi

    def order_openapi():
        schema = default_openapi()
        for path in schema["paths"].values():
            for operation in path.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        return schema

    application.openapi = order_openapi
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080, workers=1)
