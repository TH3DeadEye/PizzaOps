# PizzaOps proposed API contracts

Status: planning specification, 2026-11-06. Not implemented. Read alongside ARCHITECTURE.md. These choices resolve the open decisions in PROJECT_GUIDE.md; the synchronous cooking response described there is superseded in this proposal.

## Common rules

- JSON uses camelCase. Python implementation names use snake_case.
- Bind to `127.0.0.1`: Order 8080, Kitchen 8081, Inventory 8082. One process per service.
- All service state belongs to one `runId`. The launcher supplies the same run ID to all services. A new process creates a new `instanceId`, even if its run ID was reused.
- Every business request sends `X-Run-Id` and `X-Trace-Id`. Order generates a trace ID if missing; other entry points generate one only when independently invoked. Propagate the same trace ID downstream. Reject a mismatched run ID with `409 RUN_MISMATCH` before mutation. Health may be queried without either header.
- UUIDs identify requests, orders, jobs, reservations, runs, and instances. Validate lengths and types. Reject booleans, floats, and numeric strings for integer quantities. Reject unknown fields in business request models.
- Order quantity: 1–20. Ingredient quantities: 1–10,000 per mutation. Available stock cap: 1,000,000 per ingredient. Limits are explicit configuration with boundary tests.
- Normalize all malformed bodies, schema errors, and invalid quantities to `400 INVALID_REQUEST`, deliberately overriding the framework's default validation response.
- Accept only known pizza and ingredient identifiers. Unknown pizza is 400 `UNKNOWN_PIZZA`; unknown ingredient is 404 `UNKNOWN_INGREDIENT`. Restocking an unknown ingredient does not silently create a new one in this proposal.
- Common error: `{ "code": "INSUFFICIENT_STOCK", "message": "Not enough CHEESE", "traceId": "uuid", "details": {} }`. Do not expose stack traces or replace structured fields with dialogue.
- Include `X-Trace-Id`, `X-Run-Id`, and `X-Instance-Id` response headers. Identity values used for reconciliation must match the live run and the previously observed service instance.

## Order — :8080

### POST /orders

```json
{
  "requestId": "uuid",
  "customer": {"id": "uuid", "appearance": "customer_amber", "trait": "patient"},
  "pizzaType": "CHEESE",
  "quantity": 2
}
```

Traits initially: `patient`, `impatient`, `chatty`; appearances: the three supplied customer asset IDs. Traits affect authored dialogue and arrival generation, not inventory correctness. Use the full normalized customer/order payload when computing the request fingerprint.

New order: `201`, `Location: /orders/{orderId}`:

```json
{
  "orderId": "uuid",
  "requestId": "uuid",
  "status": "RECEIVED",
  "failureReason": null,
  "lastError": null,
  "traceId": "uuid",
  "version": 1
}
```

Duplicate identical request: `200`, current original order, original trace ID, no new queue entry. Conflicting payload under the same key: 409. Queue full: 503 `QUEUE_FULL` plus `Retry-After`, with no accepted order. Validation, duplicate lookup, admission, order creation, and RECEIVED event must have a coherent atomic boundary.

### GET /orders/{orderId}; GET /orders

Return the order's identifying fields, customer, pizza, quantity, status, failure reason, last observed error, version, and timestamps. List uses `limit` (1–100, default 50) plus an opaque cursor for pagination. Unknown order: 404 `ORDER_NOT_FOUND`.

States: `RECEIVED`, `CHECKING`, `COOKING`, `RECONCILING`, `READY`, `FAILED`, `UNKNOWN`. READY requires confirmed Kitchen COOKED. FAILED requires a known rejection or unsuccessful cooking outcome. UNKNOWN carries `lastError` and an unresolved-outcome explanation; it must not be counted as FAILED. READY and known FAILED records are terminal. UNKNOWN may later re-enter RECONCILING when new evidence is available. CHECKING may transition directly to READY if polling missed COOKING; never synthesize a cooking delay after confirmed completion.

## Kitchen — :8081

### POST /cook

```json
{"orderId": "uuid", "pizzaType": "CHEESE", "quantity": 2}
```

New job: `202`, `Location: /cook/{orderId}`:

```json
{"jobId": "uuid", "orderId": "uuid", "status": "QUEUED", "result": null, "failureReason": null, "version": 1, "traceId": "uuid"}
```

Duplicate identical job: 200 with its current record, even when completed. Conflicting payload: 409. Queue full or explicitly unavailable before acceptance: 503 with no job created. A trace is correlation metadata, not the idempotency key.

### GET /cook/{orderId}

Returns current job including `reservationId`, `deadlineAt`, `lastError`, and nullable `startedAt` and `completedAt` timestamps. Job states: `QUEUED`, `CHECKING`, `COOKING`, `COOKED`, `FAILED`, `RECONCILING`, `UNKNOWN`. A returned job with a business failure still uses HTTP 200; failure is recorded in its status and reason. The POST's 202 never means the pizza is ready. A nonexistent job returns 404 `COOK_JOB_NOT_FOUND`.

When Order receives a missing-job response after a lost submission response, it may resubmit the original payload with the same key only in the same Kitchen instance. If Kitchen restarted, reconcile with the available surviving evidence or report state loss; do not assume nothing previously happened.

### GET /mood; POST /admin/reconcile

Mood returns `{patience, stress, trust, failedReservationCount, mood, version}`. Initial patience 85, trust 60, stress 0; values clamped to 0–100. One confirmed stale-stock contradiction: trust -25, patience -10. Successful reservation: trust +5. Trust below 50 requests authoritative stock.

Reconcile is a personality recovery command with a stable `commandId`. Repeating it is deduplicated; it changes mood only, never stock or an order result. Define cooldown and recovery increments in configuration and record each adjustment.

## Inventory — :8082

### GET /stock?authoritative=false

Return all ingredient quantities in one snapshot; optional `ingredient=CHEESE` selects one. Responses include `items`, `source` (`CACHED` or `AUTHORITATIVE`), `stockVersion`, and `snapshotVersion`. An authoritative request bypasses stale-read injection, but remains subject to explicit delay/unavailability tests. With stale mode disabled, the normal response is authoritative.

Initial proposed stock: DOUGH 40, SAUCE 40, CHEESE 30, PEPPERONI 15, BASIL 15. All quantities are abstract recipe units. Stock version increments on successful reservation and restock.

### POST /reservations

```json
{"orderId": "uuid", "items": {"DOUGH": 2, "SAUCE": 2, "CHEESE": 2}}
```

Success or identical successful replay: 200:

```json
{"reservationId": "uuid", "orderId": "uuid", "status": "RESERVED", "items": {"DOUGH": 2, "SAUCE": 2, "CHEESE": 2}, "stockVersion": 2, "traceId": "uuid"}
```

Under one lock: check existing successful key/fingerprint; validate every ingredient; check every quantity; deduct all; store the immutable reservation and result; increment stock version; append event. No partial deduction. Concurrent identical requests return the same reservation. Concurrent different payloads under one successful key produce 409 `IDEMPOTENCY_CONFLICT`.

Shortage: 409 `INSUFFICIENT_STOCK`, with missing amounts and authoritative stock version in `details`. Unknown ingredient: 404. Neither changes stock or creates a successful reservation. A reservation rejected before allocation may succeed when retried after restocking, but a Kitchen job already recorded FAILED is not automatically reopened; a new customer order is the normal business retry.

### GET /reservations/{orderId}

200 returns the successful original reservation; 404 `RESERVATION_NOT_FOUND` otherwise. This lookup bypasses stale-stock behavior. A 404 does not prove that an older in-flight request cannot still complete. Safe recovery uses the original idempotency key and the same service instance, with serialized reservation handling.

RESERVED here means ingredients were permanently allocated out of available stock. No TTL, release, cancellation refund, or commit endpoint is included in v1. A lost response never restores stock automatically.

### POST /admin/restock

Body: `{commandId, items: {CHEESE: 10}}`. Atomically add all valid items or none. Dedupe command ID and normalized payload so a supplier retry cannot double restock. Success: 200 with current stock/version. Unknown ingredient: 404; exceeded cap or conflicting command: 409. Positive integer validation applies.

### POST /admin/faults

Body: `{commandId, staleStockEnabled, delayMs, failureMode, delayPhase}`. Apply an idempotent full configuration update. Bound delay to 0–10,000 ms. `failureMode` initially `NONE` or `UNAVAILABLE`; `delayPhase` initially `BEFORE_MUTATION`, with `AFTER_MUTATION_BEFORE_RESPONSE` added for response-loss acceptance tests.

On a false-to-true stale flag transition, capture one frozen stock snapshot and its version. Repeating the same configuration does not recapture it. Mutations always use real stock. Apply injected delay outside locks. Fault effects, their phase, and active configuration are observable. Administrative endpoints, health, event feeds, and snapshots are excluded from injected business-endpoint failures so tests and recovery remain observable.

## Shared read endpoints

- `GET /health`: 200 `{status: "UP", service, runId, instanceId}` for process liveness, without checking downstream dependencies. The launcher checks all three identities and liveness separately.
- `GET /snapshot`: service-owned public state and its matching `{instanceId, sequence}` watermark, captured atomically. Do not expose locks, task objects, or mutable dictionaries directly.
- `GET /events?instanceId=...&after=...&limit=...`: events strictly after the cursor, next cursor, and `hasMore`. Default limit 100, maximum 500. The viewer drains pagination before its next regular poll.
- Expired/foreign cursor: 410 `EVENT_CURSOR_RESET` with current instance and earliest/latest available sequence. Take a new snapshot, then resume after its watermark. Invalid or future sequence: 400 `INVALID_CURSOR`.

An event has this envelope:

```json
{
  "schemaVersion": 1,
  "runId": "uuid",
  "instanceId": "uuid",
  "service": "kitchen",
  "sequence": 12,
  "timestamp": "2026-11-06T23:00:00.000Z",
  "type": "COOK_STARTED",
  "traceId": "uuid",
  "orderId": "uuid",
  "entityId": "uuid",
  "entityVersion": 3,
  "payload": {"jobId": "uuid", "status": "COOKING"}
}
```

Event identity is `(runId, service, instanceId, sequence)`. Order ID is nullable for restock and global mood events. Entity versions apply to the relevant service-owned record. Use timestamps for display, not deduplication or guaranteed cross-service ordering.

Starting event catalog:

| Owner | Events |
| --- | --- |
| Order | ORDER_RECEIVED, ORDER_CHECKING, ORDER_COOKING, ORDER_RECONCILING, ORDER_READY, ORDER_FAILED, ORDER_UNKNOWN |
| Kitchen | COOK_QUEUED, COOK_STARTED, COOK_COMPLETED, COOK_FAILED, COOK_RECONCILING, COOK_UNKNOWN, RELATIONSHIP_CHANGED, MOOD_CHANGED |
| Inventory | STOCK_READ, RESERVATION_SUCCEEDED, RESERVATION_REJECTED, STOCK_RESTOCKED, FAULT_CONFIG_CHANGED |
| Each service | DEPENDENCY_TIMEOUT, DEPENDENCY_UNAVAILABLE, SERVICE_STARTED, SERVICE_STOPPING |

Every event type needs a concrete payload schema before its implementation milestone is complete. Use enums/typed models and contract tests, not unvalidated arbitrary event dictionaries.

## Failure mapping and reconciliation

| Observation | Handling |
| --- | --- |
| Inventory authoritative shortage | Kitchen FAILED / INSUFFICIENT_STOCK; Order FAILED after observing that job |
| Unknown ingredient from a validated recipe | Kitchen FAILED / RECIPE_CONFIGURATION_ERROR; retain underlying UNKNOWN_INGREDIENT in diagnostic details |
| Kitchen rejects new submission with known queue-full or unavailable response | Order may retry once within admission budget; if no acceptance occurred, FAILED with that known reason |
| Inventory rejects before mutation with a known 503 | Kitchen retries within policy; known nonallocation failure may become FAILED / INVENTORY_UNAVAILABLE |
| Dependency response lost or timeout after possible acceptance | Look up the original key; RECONCILING, not immediate definitive failure |
| Original record found | Resume its state or confirmed final result, never repeat a side effect with a new key |
| Service instance changed and evidence lost | UNKNOWN / DEPENDENCY_STATE_LOST; no invented failure or automatic reallocation |
| Reconciliation budget exhausted | UNKNOWN / FULFILLMENT_UNCONFIRMED; preserve last timeout/error and any confirmed reservation |

An explicit non-mutating rejection after an earlier ambiguous request does not erase that earlier uncertainty. Track the whole attempt history. Trace IDs identify the incident; idempotency keys identify the business operation. Logs and replay files preserve enough evidence to explain both.

See ARCHITECTURE.md for timeout defaults, queue ownership, build branches, and acceptance tests. These contracts intentionally do not claim durability or exactly-once execution across service restarts.
