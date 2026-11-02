# PizzaOps Project Guide

Status as of 2026-11-02.

This file has three jobs:

1. Explain how the finished system should work (section 2).
2. Record what has actually been built (section 3).
3. Say what to do next, step by step (sections 4 and 5).

Companion documents:

- The proposal: `~/Downloads/PizzaOps Project Proposal and Requirements.pdf`. Requirement IDs such as FR-14 and NFR-3 come from it.
- `README.md`: how to run and test the Inventory service.

You write the code. This guide gives you the concepts, the design questions, and a check for each step.

---

## 1. The idea in one paragraph

PizzaOps is a restaurant simulation made of three separate Spring Boot apps (Order, Kitchen and Inventory) that talk only over HTTP. You place pizza orders and watch the services cooperate, argue and recover. Every joke in the logs sits on top of a real distributed-systems idea: stale data, timeouts, retries and idempotency. The pitch is "a comedy management game where every bug is a real HTTP failure I can explain."

Version 1 succeeds when one person can run three apps, cause a disaster on purpose, find the exact request that caused it, and recover.

**Goals**

| ID | Goal |
|---|---|
| G1 | Three separate apps on ports 8080, 8081 and 8082, talking only over HTTP |
| G2 | Every order ends READY or FAILED, with a reason |
| G3 | A scripted Inventory "lie" makes Kitchen lose trust, and the next order behaves differently |
| G4 | Any failure can be followed across all three services with one trace ID |
| G5 | A playable 5-minute shift with a score, a reputation bar and one level |
| G6 | You can explain REST design, timeouts, status codes, idempotency and fault injection in your own words |

**Non-goals for version 1:** real payments or customer data, Kubernetes, cloud, Unity, an LLM, service discovery, message queues. The proposal also lists Docker and a database as non-goals ("Docker comes later, if at all", "State stays in memory at first"). The current code breaks that rule. See section 4, decision 1.

---

## 2. How the system should work

### 2.1 Three services, three owners

```
   you (curl now; game and browser later)
        |
        |  POST /orders
        v
 +----------------+   POST /cook     +-----------------+   GET /stock             +------------------+
 |  OrderService  | ---------------> | KitchenService  | ------------------------> | InventoryService |
 |     :8080      | <--------------- |      :8081      | <------------------------ |      :8082       |
 +----------------+  COOKED / error  +-----------------+   POST /reservations      +------------------+
                                                           stock / reservation / error
 owns: order           owns: cooking decisions,                   owns: the real stock
 lifecycle             recipes, mood (patience, trust)
```

| Service | Port | Owns | Never does |
|---|---|---|---|
| Order | 8080 | Order IDs, trace IDs, the order lifecycle, the final READY or FAILED verdict, and later the score | Touch stock or decide how to cook |
| Kitchen | 8081 | Recipes (pizza type to ingredients), mood, the decision whether to trust Inventory | Store orders or stock |
| Inventory | 8082 | The true stock numbers and reservations | Know anything about pizzas, moods or orders beyond an order ID |

### 2.2 Rules that apply everywhere

- **HTTP only.** A service never calls another service's Java classes. Replies travel back along the same path.
- **No shared code library** (NFR-5). Each service has its own copy of small things such as the trace filter and the error record.
- **Every request carries `X-Trace-Id`.** Order creates it, and Kitchen and Inventory pass it along. Every service logs it.
- **One error shape everywhere:** `{ code, message, traceId }`. A joke line may never replace a status code or an error object (NFR-1).
- **Truthfulness.** If a call gets no response, it shows as a timeout, never as an invented result. Order never says READY unless Kitchen really said COOKED (FR-5).
- **Fault controls are localhost-only** (NFR-7). All services bind to `127.0.0.1`.
- **Determinism.** Faults use a seeded random generator, so the same seed reproduces the same disaster (NFR-2). Inventory's lies are explicit fault injections; the real stock is never corrupted.
- **Timeouts.** The default is 2 seconds between services, and it must be configurable. A happy-path order should finish in under 1 second (NFR-6).

### 2.3 The happy path

1. **Client to Order:** `POST /orders {pizzaType, quantity}`.
2. **Order validates.** Quantity of zero or less gets a 400 and a clear error (FR-2). Otherwise Order creates an `orderId` and a `traceId`, stores the order as RECEIVED, and logs it.
3. **Order to Kitchen:** `POST /cook {orderId, pizzaType, quantity}` with `X-Trace-Id`.
4. **Kitchen decides.** It looks up the recipe, checks its mood, and (see 2.6) looks at stock.
5. **Kitchen to Inventory:** `POST /reservations` to reserve the ingredients.
6. **Inventory reserves atomically** and answers 200 with a `reservationId`.
7. **Kitchen answers Order:** 200 `{result: COOKED}`. Kitchen's trust in Inventory rises by 5.
8. **Order marks the order READY** and returns or exposes `{orderId, status, traceId}`.

Done when (milestone P0): one curl request ends in READY with a trace ID, and the same trace ID appears in all three logs.

### 2.4 Order lifecycle

```
RECEIVED --> CHECKING --> COOKING --> READY
                 |           |
                 +-----------+--------> FAILED (always with a reason)
```

Reasons named in the proposal: `INSUFFICIENT_STOCK`, `KITCHEN_TIMEOUT`. You will need more (see section 4, gap 3).

### 2.5 Failure paths

| What goes wrong | Who notices | What Order ends with |
|---|---|---|
| Quantity is 0 or negative | Order, immediately | 400, no order created |
| Not enough ingredients | Inventory answers 409 on reserve | Kitchen answers 409 `INSUFFICIENT_STOCK`, so the order is FAILED with that reason |
| Inventory is slow | Kitchen's call to Inventory times out | Kitchen reports a failure. Order may time out too, giving FAILED `KITCHEN_TIMEOUT` |
| Kitchen is in a bad mood (patience below 25) | Kitchen returns a simulated 503 `KITCHEN_UNAVAILABLE` for a short time | FAILED with a clear reason |
| Inventory returns 503 (test mode) | Kitchen | FAILED with a clear reason (the reason name is up to you) |
| Inventory lies about stock | Kitchen's reservation fails after the stock check said yes | FAILED now. Kitchen's trust drops, so the next order behaves differently (see 2.6) |

### 2.6 Kitchen personality: state plus rules, not generated text

Events change state. State changes decisions. A short list of authored lines (5 to 10 per mood) makes the change visible.

Kitchen keeps `patience`, `trust` (in Inventory) and `failedReservationCount`.

| Event | Effect |
|---|---|
| Reservation succeeds | Trust +5, capped at 100 |
| Inventory claims stock but the reservation fails | Trust -25, patience -10, a `RelationshipChanged` event is logged |
| Trust below 50 | Kitchen demands an authoritative stock check on every order |
| Patience below 25 | Kitchen may return a simulated 503 for a short time |
| Quiet time, or a reconcile action | Patience recovers gradually |
| Any forced failure | A cooldown stops one bad response from causing an endless failure loop |

The reconcile action changes personality state only, never the real stock (FR-20). The mood rules should be plain Java classes you can unit-test without starting Spring (NFR-9).

**How the "lie" works:**

- Kitchen asks Inventory `GET /stock`. Kitchen reads the answer as "claims stock", and this is my interpretation of FR-9.
- With `staleStockEnabled` on, Inventory answers from an old snapshot and marks it `source: CACHED`.
- The snapshot says "plenty of cheese". The real stock is zero.
- Kitchen then calls `POST /reservations`. Reservations always check the true quantity, so Inventory answers 409.
- Kitchen notices the contradiction, and trust drops.

### 2.7 Inventory fault injection

`POST /admin/faults {staleStockEnabled, delayMs, failureMode}` switches these on and off:

- **Stale stock:** `GET /stock` returns old numbers (FR-16).
- **Delay:** responses are slowed by `delayMs`, to provoke timeouts (FR-17).
- **Failure mode:** simulated 503s.

Reservations must stay correct under all faults.

### 2.8 Logging and tracing

One log line per event, with timestamp, service name, event type, order ID and trace ID (FR-19). The format should be identical in all three services, so `grep <traceId>` across three logs tells the whole story of one incident. Example event types: `ORDER_RECEIVED`, `COOK_REQUESTED`, `RESERVATION_FAILED`, `RELATIONSHIP_CHANGED`, `ORDER_FAILED`.

### 2.9 Game layer (after the core works)

- A shift lasts 5 minutes and orders arrive automatically.
- READY earns +10 points. FAILED costs 5 points and 10 reputation.
- Reputation starts at 100, and the restaurant closes at 0.
- Score reads the true final state from Order, never a log line.
- Manager actions (reconcile staff, restock cheese, restart a service) have a cost or a cooldown.
- **Level 1, "The Cheese Shortage":** serve 20 orders with fewer than 3 failures.

### 2.10 Target API contracts (from the proposal)

| Service | Endpoint | Request | Success | Errors |
|---|---|---|---|---|
| Order | `POST /orders` | pizzaType, quantity | 201 `{orderId, status, traceId}` | 400 invalid quantity |
| Order | `GET /orders/{id}` | none | 200 `{status, failureReason, ...}` | 404 unknown order |
| Order | `GET /orders` | none | 200 recent orders | none |
| Kitchen | `POST /cook` | orderId, pizzaType, quantity | 200 `{result: COOKED}` | 409 `INSUFFICIENT_STOCK`, 503 `KITCHEN_UNAVAILABLE` |
| Kitchen | `GET /mood` | none | 200 patience, trust, mood | none |
| Inventory | `GET /stock?ingredient=` | none | 200 `{ingredient, quantity, source}` where source is CACHED or AUTHORITATIVE | 404 unknown ingredient |
| Inventory | `POST /reservations` | orderId, ingredient, quantity | 200 `{reservationId}` | 409 not enough stock |
| Inventory | `POST /admin/faults` | staleStockEnabled, delayMs, failureMode | 200 | 400 invalid setting |
| All | `GET /actuator/health` | none | 200 `UP` | none |

---

## 3. What has been built

### 3.1 Summary

| Service | State |
|---|---|
| Inventory (8082) | Built and tested, but it needs Docker and PostgreSQL (against the proposal) |
| Kitchen (8081) | Does not exist (no folder, no build file, not in `settings.gradle`) |
| Order (8080) | Empty Spring Boot starter plus one `Order` record. No web dependency, so it cannot serve HTTP yet |

The Gradle build configures and both modules compile (checked 2026-11-02). I have not run the app or the tests, because Inventory needs Docker. Step 0 below is for you to run them.

### 3.2 Inventory in detail

**Endpoints that exist today**

| Endpoint | Behaviour |
|---|---|
| `GET /stock?ingredient=` | 200 `{ingredient, available}` or 404 `UNKNOWN_INGREDIENT` |
| `POST /admin/restock {ingredient, quantity}` | Adds stock and creates the ingredient if new. 409 `STOCK_LIMIT_EXCEEDED` on integer overflow |
| `POST /reservations {orderId (UUID), items: {ingredient: qty}}` | 200 `RESERVED`. A repeat of the same order and items returns the original and deducts nothing. Same order ID with different items gives 409 `IDEMPOTENCY_CONFLICT`. A shortage gives 409 `INSUFFICIENT_STOCK` and nothing is deducted. An unknown ingredient gives 404 |
| `GET /actuator/health` | 200 `UP` |
| any bad input | 400 `INVALID_REQUEST` |

Every response has an `X-Trace-Id` header. Errors use `{code, message, traceId}`.

**Code map** (`inventory-service/src/main/java/ca/milani/arman/pizzaops/inventoryservice/`)

| Class | Job |
|---|---|
| `web/TraceIdFilter` | Reads or generates the trace ID, puts it in the log context, echoes it in the response |
| `controller/InventoryController` | Three endpoints; validates request bodies |
| `service/InventoryService` | All decisions, inside a transaction |
| `repository/*` | Database access (JPA plus two native `INSERT ... ON CONFLICT` queries) |
| `model/StockItem`, `model/Reservation` | Database tables |
| `dto/*` | Request and response shapes |
| `exception/*` | Turns errors into the `{code, message, traceId}` body |

**How `reserve()` works** (`InventoryService.java:52`). Learn this one well; it covers NFR-3, FR-14 and FR-15.

1. Claim the order ID by inserting a row with `ON CONFLICT DO NOTHING`. Simultaneous duplicate requests are serialized by the database here.
2. If the row already existed, return the original reservation. If the items differ, answer 409 `IDEMPOTENCY_CONFLICT`.
3. Lock the stock rows for the requested ingredients with `SELECT ... FOR UPDATE`, always in alphabetical order. That consistent order prevents deadlocks.
4. Check every ingredient before deducting any.
5. Deduct. If anything threw, the whole transaction rolls back, including the order-ID claim, so a retry after restocking works.

**Tests:** 11 HTTP integration tests, including 12 parallel requests competing for 3 units of cheese, duplicate-request races, rollback checks, and lock-ordering. They need the test database (`docker compose --profile test up -d --wait inventory-test-db`).

**How to run:** see `README.md`. It needs Docker, a Postgres container on `localhost:55432`, and `./gradlew :inventory-service:bootRun`.

### 3.3 Requirement scorecard

| Requirement | Status |
|---|---|
| FR-1 to FR-6 (Order) | Missing |
| FR-7 to FR-12 (Kitchen, mood) | Missing |
| FR-13 `GET /stock` | Partial (field is `available`, no `source`) |
| FR-14 atomic reserve + 409 | Done (request shape differs from the proposal) |
| FR-15 idempotent reservations | Done (a P2 item, built early) |
| FR-16 stale stock, FR-17 delay and 503 | Missing |
| FR-18 health | Inventory only |
| FR-19 one structured log line per event | Not met (trace ID is logged, but no service name, event type or order-ID field) |
| FR-20 reconcile | Missing |
| NFR-1 error shape | Inventory only |
| NFR-2 seeded randomness | Missing |
| NFR-3 concurrency | Met and well tested |
| NFR-4 observability across services | Inventory only |
| NFR-5 each service runs alone | Not met (Inventory needs Postgres) |
| NFR-6 timeouts and speed | Missing (belongs in the callers) |
| NFR-7 localhost only | Met for Inventory |
| NFR-8 no Docker needed | Violated |
| NFR-9 mood rules testable without Spring | Missing |

### 3.4 Where the code differs from the proposal

- **Database and Docker.** The proposal says in-memory and no Docker. The code uses PostgreSQL via Docker Compose, JPA, `ddl-auto=update` and Postgres-specific SQL.
- **Reservation shape.** The proposal says one `ingredient` and `quantity`. The code takes a map of items, which implies a recipe table somewhere.
- **`orderId` must be a UUID.** Otherwise Inventory answers 400. Order must generate UUIDs.
- **Stock response.** The field is `available`, not `quantity`, and `source` is missing.
- **Extras.** `/admin/restock` is a sensible addition, since "restock cheese" is a manager action. `STOCK_LIMIT_EXCEEDED` and the lock-ordering tests go beyond the proposal.
- **Order model.** `Order(id, customerId, pizzaId, size, status)` does not match the proposal, which needs `pizzaType`, `quantity`, `status`, `failureReason` and `traceId`. It is a record, which cannot change state. `customerId` and `size` are not in the proposal.
- **Repo clutter.** The root `src/` `DemoApplication` is unused. `order-service` has its own `settings.gradle` and `gradlew`, and `inventory-service` does not.

---

## 4. Open decisions and gaps

### Decisions you must make

1. **Persistence for Inventory.**
   - **A. Rebuild Inventory in memory yourself (recommended).** It matches the proposal and removes Docker as a prerequisite. It teaches the concurrency lesson first-hand, and stale-stock and delay faults are simpler. The existing HTTP tests act as your answer key. Most pass unchanged; the two helpers that use `JdbcTemplate` need replacing.
   - **B. Keep Postgres and update the proposal** (NFR-8, the non-goals, and the milestones). Fine, but then you must be able to explain `FOR UPDATE`, `ON CONFLICT` and lock ordering in your own words (G6).
2. **Recipes.** Is one pizza equal to one unit of one ingredient (the proposal's assumption), or does Kitchen own a small recipe table? The current Inventory API already accepts several ingredients per order. A table with two or three pizzas makes "The Cheese Shortage" more interesting, because cheese-free pizzas keep working.
3. **Sync or async.** Does `POST /orders` block until the order is READY or FAILED, or does it return `RECEIVED` at once while you poll `GET /orders/{id}`? The P0 milestone ("one curl returns READY") suggests synchronous. Synchronous is simpler to start with, but then `CHECKING` and `COOKING` are only visible in logs.
4. **Dashboard location** (open question in the proposal): inside Order, or a fourth app. Not needed before P3.

### Gaps in the proposal itself

1. **No way to request authoritative stock.** FR-10 needs Kitchen to demand it, but `GET /stock?ingredient=` has no parameter for that. Design one.
2. **Timeouts stack.** If every hop uses 2 seconds, Kitchen and Order give up at the same moment. Order can then report `KITCHEN_TIMEOUT` while Kitchen is still working and makes a reservation afterwards. What should the relationship between the two timeouts be? What happens to the orphaned reservation? (Idempotent reservations are part of the answer.)
3. **No failure-reason catalog.** For each failure at each hop, write down what Kitchen returns and what Order stores. Include Inventory 503, Inventory timeout, Kitchen 503 and unknown ingredient.
4. **The trust arithmetic does not produce the G3 demo.** Trust is capped at 100 and one incident costs 25. One incident leaves 75, which is not below 50. It takes three incidents (100, 75, 50, 25) to trigger the authoritative check, but the P1 test expects the second order to behave differently after one incident. Choose a lower starting trust (74 or less), a bigger drop, or a different threshold. The proposal says these numbers are starting values to tune.
5. **What does "old" mean for stale stock?** Old compared to what, and captured when? A snapshot taken at the moment the flag is switched on is one answer.
6. **The proposal does not give `RESERVATION_FAILED`-style event names or the log format.** Define them once and reuse them in all three services.

---

## 5. Next steps

Do these in order. For each step: what you will learn, the questions to answer yourself, and how to know it is done. Bring me your answers and your code, and I will review them.

### Step 0: Run and poke Inventory (about an hour)

*Learn:* what the existing service actually does, before you change anything.

Follow `README.md`: start Docker, start the database, then run `./gradlew :inventory-service:bootRun`. Then work through the "Try the first workflow" curl commands. Try each of these and write down the status code and body you expect **before** you press enter:

- Repeat the same reservation. Why does cheese stay at 8?
- Reuse the order ID with different items.
- Reserve more cheese than exists.
- Reserve cheese plus an ingredient that doesn't exist. Is cheese deducted?
- Send malformed JSON, and an uppercase ingredient name.
- Call `/actuator/health`, and send your own `X-Trace-Id`. Find it in the log.

Then run the test suite (`docker compose --profile test up -d --wait inventory-test-db`, then `./gradlew :inventory-service:test`).

*Done when:* you can predict every response, and you can explain the five steps of `reserve()` in your own words.

### Step 1: Decide persistence (decision 1 above)

*Learn:* the cost of "free" infrastructure, and how to change a decision on purpose rather than by accident.

*Done when:* you have written your choice, and the reason, at the top of `README.md` or this file.

If you pick path A, work out these before you code:

- What replaces the database row lock?
- How do you make "check every ingredient, then deduct all" atomic across two ingredients?
- What happens when two identical requests arrive at the same moment?
- A single lock around the whole reservation is acceptable at this scale. Why? When would it stop being acceptable?

### Step 2: Write the contract sheet (no code)

*Learn:* contract-first design. Most distributed bugs live between services, not inside them.

Create a short `CONTRACTS.md` containing:

- The exact request and response for `POST /cook` and `POST /orders`.
- The reservation shape (single ingredient or items map) and where recipes live.
- The authoritative-stock parameter (gap 1).
- The failure-reason table (gap 3).
- The timeout budget with its inner-versus-outer rule (gap 2).
- Sync or async (decision 3).
- The starting trust value (gap 4).
- The log format and event names (gap 6).

*Done when:* someone could write Kitchen using only that sheet.

### Step 3: Align Inventory with the contract

*Learn:* changing an API without breaking its callers.

- `GET /stock` returns `quantity` and a `source` field. For now every answer is `AUTHORITATIVE`.
- Add whatever authoritative-stock option you chose in Step 2.
- Update the tests, then your own logging to the shared format.

*Done when:* the tests pass and `curl` shows the new fields. Leave faults for later.

### Step 4: Build Kitchen (port 8081)

*Learn:* calling another service with `RestClient`, timeouts, and translating errors between layers.

- Add a `kitchen-service` module and register it in `settings.gradle`.
- Dependencies: web, actuator, validation. Bind to localhost only.
- Give Kitchen its own copy of the trace filter and error shape (NFR-5: no shared library).
- Put the Inventory base URL and the timeout in `application.properties`.
- `POST /cook` looks up the recipe, calls Inventory, and maps each outcome to your failure table.
- No mood yet. That is P1.

Questions to answer first:

- What should Kitchen do when Inventory is down? When it is slow?
- Where is the line between a Kitchen error and an Inventory error in the response?

*Done when* (all with curl, no Order needed):

- With stock, `POST /cook` returns 200 `COOKED` and the stock drops.
- With too little stock, you get 409 `INSUFFICIENT_STOCK` in the standard error shape.
- With Inventory stopped, you get a clean error within the timeout. There is no hang and no raw stack trace.
- The same trace ID appears in both logs.

### Step 5: Build Order (port 8080)

*Learn:* state machines, owning the truth, and why "READY" must be earned.

- Fix the dependencies (web, actuator, validation). Replace the `Order` model with the proposal's fields.
- Use a status type that can only hold the five states. Store orders in a thread-safe in-memory structure.
- Implement `POST /orders`, `GET /orders/{id}` and `GET /orders`, validation, trace ID creation, and the call to Kitchen.
- Map every Kitchen outcome to a `failureReason` (your table from Step 2).
- Never report READY unless Kitchen said COOKED.

*Done when* (milestone P0):

- One `curl POST /orders` ends in READY with a trace ID.
- `grep` for that trace ID finds lines in all three logs.
- A shortage produces FAILED with `INSUFFICIENT_STOCK`.
- Stopping Kitchen produces FAILED with a sensible reason.
- `/actuator/health` is UP on all three ports.

### Step 6: P0 acceptance tests

Write the first two rows of the proposal's test table as integration tests: normal path, and insufficient stock. The "dependency timeout" test needs the delay fault (FR-17), so it comes in P2.

### After P0: decide whether to keep going

The proposal says to stop after P1 and decide. A first version that makes you smile counts as finished.

| Milestone | What it adds |
|---|---|
| P1: the first argument | Stale-stock flag, Kitchen mood and trust as plain unit-tested Java classes, `RelationshipChanged` events, authored lines |
| P2: playable chaos | Delay and 503 modes, `/admin/faults`, seeded randomness, retry limit with idempotent reservations, reconcile action, cooldowns |
| Game layer | Shift timer, score, reputation, Level 1 |
| P3 (optional) | Browser control room: plain HTML and polling first, then server-sent events |

**The Later list** (do not start these before P1 works): Docker Compose, Kubernetes, a message queue, service discovery, an LLM, Unity.

---

## 6. Self-check for G6

You are done learning each topic when you can answer these without looking:

- Why does a repeated `POST /reservations` not deduct stock twice, and what stops two simultaneous duplicates?
- What does 409 mean, and how is it different from 400, 404 and 503?
- Why is a timeout different from an error response, and what must a caller assume after one?
- Why must the outer timeout be longer than the inner one?
- What would break if two reservations locked their ingredients in opposite order?
- What does "stale" mean here, and why must reservations ignore it?
- Why does the log line carry both an order ID and a trace ID?
- What is fault injection, and why is it safe here (seeded, explicit, never corrupting real stock)?
