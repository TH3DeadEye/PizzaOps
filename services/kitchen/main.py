from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from services.kitchen.errors import ErrorResponse, KitchenError
from services.kitchen.events import EventPage, LOGGER, configure_logging
from services.kitchen.inventory_client import InventoryClient
from services.kitchen.jobs import Kitchen
from services.kitchen.models import (
    CookRequest, HealthResponse, Job, MoodState, ReconcileRequest, ReconcileResponse, Snapshot,
)
from services.kitchen.settings import Settings
from services.kitchen.trace import read_trace


async def require_run(request: Request, run_id: Annotated[UUID, Header(alias="X-Run-Id")],
                      trace_id: Annotated[UUID | None, Header(alias="X-Trace-Id")] = None):
    kitchen = request.app.state.kitchen
    if run_id != kitchen.settings.run_id:
        raise KitchenError(409, "RUN_MISMATCH", "Request belongs to a different simulation run",
                           {"runId": str(kitchen.settings.run_id)})
    request.state.trace_id = trace_id or request.state.trace_id
    return kitchen


Service = Annotated[Kitchen, Depends(require_run)]


def default_kitchen(settings):
    return Kitchen(settings, InventoryClient(settings))


def create_app(settings=None, *, kitchen_factory=default_kitchen):
    settings = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        configure_logging()
        kitchen = kitchen_factory(settings)
        application.state.kitchen = kitchen
        await kitchen.start()
        try:
            yield
        finally:
            await kitchen.stop()

    application = FastAPI(title="PizzaOps Kitchen", version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def correlation(request, call_next):
        request.state.trace_id = uuid4()
        try:
            request.state.trace_id = read_trace(request.headers.get("X-Trace-Id"))
            response = await call_next(request)
        except KitchenError as error:
            response = error.response(request.state.trace_id)
        except Exception:
            LOGGER.exception("Unhandled kitchen error; traceId=%s", request.state.trace_id)
            response = KitchenError(500, "INTERNAL_ERROR", "Kitchen could not complete the request").response(request.state.trace_id)
        response.headers["X-Trace-Id"] = str(request.state.trace_id)
        response.headers["X-Run-Id"] = str(settings.run_id)
        response.headers["X-Instance-Id"] = str(request.app.state.kitchen.instance_id)
        return response

    @application.exception_handler(KitchenError)
    async def domain_error(request, error):
        return error.response(request.state.trace_id)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        fields = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"], "type": item["type"]}
                  for item in error.errors()]
        return KitchenError(400, "INVALID_REQUEST", "Request validation failed", {"fields": fields}).response(request.state.trace_id)

    @application.exception_handler(HTTPException)
    async def http_error(request, error):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(error.status_code, "HTTP_ERROR")
        response = KitchenError(error.status_code, code, str(error.detail)).response(request.state.trace_id)
        for name, value in (error.headers or {}).items():
            response.headers[name] = value
        return response

    @application.get("/health", response_model=HealthResponse)
    async def health(request: Request):
        return HealthResponse(run_id=settings.run_id, instance_id=request.app.state.kitchen.instance_id)

    router = APIRouter(responses={status: {"model": ErrorResponse} for status in (400, 404, 409, 410, 503)})

    @router.post("/cook", response_model=Job, status_code=202, responses={200: {"model": Job, "description": "Existing job"}})
    async def cook(body: CookRequest, request: Request, response: Response, kitchen: Service):
        job, created = await kitchen.submit(body, request.state.trace_id)
        response.status_code = 202 if created else 200
        response.headers["Location"] = f"/cook/{job.order_id}"
        return job

    @router.get("/cook/{order_id}", response_model=Job)
    async def cook_status(order_id: UUID, kitchen: Service):
        return await kitchen.job(order_id)

    @router.get("/mood", response_model=MoodState)
    async def mood(kitchen: Service):
        return await kitchen.mood()

    @router.post("/admin/reconcile", response_model=ReconcileResponse)
    async def reconcile(body: ReconcileRequest, request: Request, kitchen: Service):
        return await kitchen.reconcile(body, request.state.trace_id)

    @router.get("/snapshot", response_model=Snapshot)
    async def snapshot(kitchen: Service):
        return await kitchen.snapshot()

    @router.get("/events", response_model=EventPage)
    async def events(kitchen: Service, instance_id: Annotated[UUID, Query(alias="instanceId")],
                     after: int = 0, limit: Annotated[int, Query(ge=1, le=500)] = 100):
        return await kitchen.events(instance_id, after, limit)

    application.include_router(router)
    default_openapi = application.openapi

    def kitchen_openapi():
        schema = default_openapi()
        for path in schema["paths"].values():
            for operation in path.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        return schema

    application.openapi = kitchen_openapi
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8081, workers=1)
