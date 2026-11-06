# PizzaOps Project Guide (Python edition)

Status as of 2026-11-06. This is the Python rewrite of the Java guide dated 2026-11-02. The Java project is left untouched at `~/Desktop/Folders/Programming/Java/PizzaOps/PizzaOps` and is now a reference only.

**Starting a new chat here?** Tell Claude: "Read PROJECT_GUIDE.md first." It carries the working agreement, the decisions and the next step.

This file has four jobs:

1. Say what changed and why (section 0).
2. Explain how the finished system should work (sections 1 and 2).
3. List what you need installed and what is still undecided (sections 3 and 4).
4. Say what to do next, step by step (sections 5 and 6).

Companion documents:

- The proposal: `~/Downloads/PizzaOps Project Proposal and Requirements.pdf`. Requirement IDs such as FR-14 and NFR-3 come from it. It still defines the behaviour; only the tech and the "game" idea changed.
- `CONTRACTS.md`: you write this in Step 1. It does not exist yet.

---

## 0. What changed and why

| | Java guide (Nov 2) | This guide (Nov 6) |
|---|---|---|
| Language and framework | Java 17, Spring Boot, Gradle | Python 3.13, FastAPI, uv |
| Inventory storage | PostgreSQL in Docker | In memory, protected by a lock |
| Calling other services | Spring `RestClient` | `httpx` with explicit timeouts |
| Tests | JUnit | pytest |
| The "game" | Player runs a 5-minute shift: score, manager actions, Level 1 | **Observer simulation:** you control nothing; characters walk and talk on their own |
| Visual | Optional browser dashboard (P3) | A pygame window that only reads events |
| Top priority | Learn distributed-systems concepts carefully | Quick visible results, while still learning the concepts |

**Why the switch.** The Java code was hard to follow, and the trouble was mostly framework magic rather than syntax: Lombok generates constructors and loggers you can't see, `@Transactional` wraps methods in invisible database transactions, and the repositories run SQL behind the scenes. You did not write that Inventory service, and Order and Kitchen were not started, so almost nothing of yours was lost. The ideas (HTTP, status codes, timeouts, idempotency, trace IDs, fault injection) are identical in Python.

**Working agreement (change it here if it changes).**
Current default: Claude is your tutor. It explains, asks guiding questions, reviews code you write, and points out gaps. It does not write the project's code for you. Claude shows a snippet only when you ask for a specific one (for example, "how do I declare one endpoint in FastAPI?"). You said "quick results" matters most, so you may loosen this: ask Claude to write plumbing (setup, boilerplate, the viewer's drawing code) while you write the parts you most want to understand (mood rules, dialogue, the reservation logic). Write the decision here: `Tutor level: ____`

---

## 1. The idea in one paragraph

PizzaOps is a restaurant simulation you watch, like an ant farm. Three separate apps (Order, Kitchen, Inventory) talk only over HTTP. Characters in a small pygame window walk between stations and talk to each other according to their personalities. Every joke sits on top of a real distributed-systems event: stale data, timeouts, retries, idempotency. The pitch: "a show where every argument between characters is a real HTTP failure I can explain."

Version 1 succeeds when you can start everything, watch an episode run by itself, spot a character complaining, and follow the exact requests behind it with one trace ID.

**Goals**

| ID | Goal |
|---|---|
| G1 | Three separate apps on ports 8080, 8081 and 8082, talking only over HTTP |
| G2 | Every order ends READY or FAILED, with a reason |
| G3 | A scripted Inventory "lie" makes Kitchen lose trust, and the next order behaves differently |
| G4 | Any failure can be followed across all three services with one trace ID |
| G5 (changed) | A watchable episode: customers arrive on their own, characters walk and talk, and the same seed replays the same episode |
| G6 | You can explain REST design, timeouts, status codes, idempotency and fault injection in your own words |

**Non-goals for version 1:** real payments or customer data, Kubernetes, cloud, Docker, a database, Unity, an LLM writing the dialogue, service discovery, message queues, and any way for the viewer to control the world. Pause, speed and seed selection are allowed because they don't steer the characters.

---

## 2. How the system should work

### 2.1 Processes and characters

```
  AUTOPILOT (one script, a plain HTTP client)       VIEWER (pygame window, read-only)
  customer generator, fault director, supplier      polls /events on all three services,
        |                                           turns events into walking and speech
        | POST /orders, POST /admin/..., restock          ^   ^   ^
        v                                                 |   |   |  GET /events
 +----------------+   POST /cook     +-----------------+  |   |   |   +------------------+
 |  OrderService  | ---------------> | KitchenService  | -+---+---+-> | InventoryService |
 |     :8080      | <--------------- |      :8081      | ------------> |      :8082       |
 +----------------+  COOKED / error  +-----------------+  GET /stock    +------------------+
                                                          POST /reservations
```

| Service | Port | Character | Owns | Never does |
|---|---|---|---|---|
| Order | 8080 | The waiter | Order IDs, trace IDs, the order lifecycle, the final READY or FAILED verdict | Touch stock or decide how to cook |
| Kitchen | 8081 | The chef (moody) | Recipes, mood (patience, trust), the decision whether to trust Inventory | Store orders or stock |
| Inventory | 8082 | The stockkeeper (may lie) | The true stock numbers and reservations | Know anything about pizzas, moods or orders beyond an order ID |

Extra cast, not services: **customers** (created by the autopilot, each with traits such as patient, picky, loud) and the **supplier** (the autopilot restocks on a schedule so the world doesn't run dry forever).

The autopilot and the viewer are clients. They use the same HTTP doors a human would. That keeps the services honest.

### 2.2 Rules that apply everywhere

- **HTTP only.** A service never imports another service's Python code. Replies travel back along the same path.
- **No shared code library** (NFR-5). Each service has its own copy of small things such as the trace-ID helper and the error shape.
- **Every request carries `X-Trace-Id`.** Order creates it, and Kitchen and Inventory pass it along. Every service logs it.
- **One error shape everywhere:** `{ code, message, traceId }`. A joke line may never replace a status code or an error object (NFR-1). Lines travel in an extra field.
- **Truthfulness.** If a call gets no response, it shows as a timeout, never as an invented result. Order never says READY unless Kitchen really said COOKED (FR-5). The viewer never decides an outcome: it only shows what happened.
- **Fault controls are localhost-only** (NFR-7). All services bind to `127.0.0.1`.
- **Determinism.** Faults, customer arrivals and traits use a seeded random generator, so the same seed reproduces the same episode (NFR-2). Inventory's lies are explicit fault injections; the real stock is never corrupted.
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

Events change state. State changes decisions. A short list of authored lines (5 to 10 per mood) makes the change visible. This is what the characters say on screen.

Kitchen keeps `patience`, `trust` (in Inventory) and `failedReservationCount`.

| Event | Effect |
|---|---|
| Reservation succeeds | Trust +5, capped at 100 |
| Inventory claims stock but the reservation fails | Trust -25, patience -10, a `RELATIONSHIP_CHANGED` event is logged |
| Trust below 50 | Kitchen demands an authoritative stock check on every order |
| Patience below 25 | Kitchen may return a simulated 503 for a short time |
| Quiet time, or a reconcile | Patience recovers gradually. In the observer version the autopilot or a timer triggers reconcile; no human does |
| Any forced failure | A cooldown stops one bad response from causing an endless failure loop |

The reconcile action changes personality state only, never the real stock (FR-20). The mood rules should be plain Python functions or classes you can unit-test without starting a web server (NFR-9).

**How the "lie" works:**

- Kitchen asks Inventory `GET /stock`. Kitchen reads the answer as "claims stock" (my interpretation of FR-9).
- With `staleStockEnabled` on, Inventory answers from an old snapshot and marks it `source: CACHED`.
- The snapshot says "plenty of cheese". The real stock is zero.
- Kitchen then calls `POST /reservations`. Reservations always check the true quantity, so Inventory answers 409.
- Kitchen notices the contradiction, and trust drops.

### 2.7 Inventory fault injection

`POST /admin/faults {staleStockEnabled, delayMs, failureMode}` switches these on and off. In the observer version the **director** in the autopilot flips them on a seeded schedule.

- **Stale stock:** `GET /stock` returns old numbers (FR-16).
- **Delay:** responses are slowed by `delayMs`, to provoke timeouts (FR-17).
- **Failure mode:** simulated 503s.

Reservations must stay correct under all faults.

### 2.8 Logging, tracing and the event feed

One line per event, with timestamp, service name, event type, order ID and trace ID (FR-19). The format should be identical in all three services, so `grep <traceId>` across three logs tells the whole story of one incident.

New in this version: the same record is also kept in a short in-memory list in each service and served at `GET /events?since=<n>`. The viewer polls that. So the **log line, the event and the speech bubble all come from one record**. Example event types: `ORDER_RECEIVED`, `COOK_REQUESTED`, `RESERVATION_FAILED`, `RELATIONSHIP_CHANGED`, `ORDER_FAILED`.

### 2.9 The show (observer layer, replaces the old "game layer")

Nothing here is controlled by you.

- **Autopilot** (one script with three jobs):
  - *Customer generator:* sends `POST /orders` at seeded intervals, with seeded customer traits.
  - *Director:* flips Inventory faults on a seeded schedule. A "scenario" such as The Cheese Shortage is just a script of timed faults.
  - *Supplier:* restocks on a schedule so the world doesn't run dry forever.
- **Viewer** (pygame): polls the three `/events` lists, merges them by time, and draws a small restaurant: a door, a counter, a kitchen, a storeroom. Each event becomes a movement or a speech bubble. Walking is animation only. An order does not wait for a customer to physically arrive.
- **Dialogue:** a function takes (event, the speaker's mood, their relationship to the other speaker) and picks an authored line. Start with 5 to 10 lines per mood. Make the text funny before you draw anything nice. An LLM could replace this function later, at the same seam, but is a non-goal for v1.
- **Optional ambient stats:** a reputation bar or served/failed counters can be shown for flavour. They don't steer anything.
- **Allowed viewer controls:** pause, speed, choose the seed.

### 2.10 Target API contracts (from the proposal, plus `/events`)

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
| Inventory | `POST /admin/restock` | ingredient, quantity | 200 | 400 invalid, 409 limit exceeded |
| All | `GET /events?since=` | none | 200 recent events | none |
| All | `GET /health` | none | 200 `UP` | none |

---

## 3. Requirements: what you need

### 3.1 Already on your Mac (checked 2026-11-06)

| Tool | Version found | Used for |
|---|---|---|
| Python | 3.13.11 (several Pythons are on your PATH, so use a virtual environment) | Everything |
| uv | 0.10.10 | Creates the environment, installs packages, runs commands |
| pip | 25.3 | Not needed if you use uv |
| git | yes (Homebrew) | Version control |
| curl | 8.7.1 | Poking the services |
| jq | yes | Reading JSON in the terminal (optional but handy) |
| VS Code (`code` command) | yes | Editor. PyCharm is not installed, which is fine |

### 3.2 Python packages to install (none are installed yet)

| Package | Why |
|---|---|
| `fastapi[standard]` | The web framework for all three services, plus `uvicorn` to run them |
| `httpx` | Calling other services, with explicit timeouts (also used by tests) |
| `pytest` | Tests |
| `pygame-ce` | The viewer window (the actively maintained pygame fork; no extra system libraries needed on macOS) |
| `ruff` (optional) | Catches mistakes and formats code |

Versions: install the latest. `uv` records exact versions in `uv.lock`.

### 3.3 Not needed

Docker, PostgreSQL, Java, Gradle, Node, a game engine, an API key, an internet connection while running (only to install packages).

### 3.4 Python you need to be comfortable with

You don't need all of this on day one. Learn it as each step asks for it.

- Functions, classes, dicts and lists, f-strings, `if` and `for`.
- Type hints (`x: int`). FastAPI and Pydantic use them to validate requests.
- Dataclasses or Pydantic models for request and response shapes.
- Exceptions: `try`, `except`, `raise`.
- `threading.Lock` (why reservations need one).
- `def` versus `async def` (see Step 2, question 1).
- `random.Random(seed)` for repeatable randomness.
- A `pygame` game loop (Step 7).

### 3.5 Suggested folder layout (you may change it)

```
PizzaOps/
  PROJECT_GUIDE.md
  CONTRACTS.md          (you write it in Step 1)
  pyproject.toml        (uv creates it)
  services/
    inventory/          each service has its own copy of the trace-ID and error helpers (NFR-5)
    kitchen/
    order/
  autopilot/
  viewer/
  tests/
```

---

## 4. Open decisions and gaps

### Decided

1. **Language and framework:** Python 3.13 with FastAPI. (Decided 2026-11-06.)
2. **Inventory persistence:** in memory with a lock. Docker and Postgres are out.
3. **The game:** observer simulation in pygame. The viewer only reads.
4. **Dashboard location** (the proposal's open question): there is no browser dashboard. The viewer is a separate client, and each service serves its own `/events`.

### Still yours to decide (answer these in `CONTRACTS.md`)

1. **Recipes.** Is one pizza equal to one unit of one ingredient (the proposal's assumption), or does Kitchen own a small recipe table? A table with two or three pizzas makes The Cheese Shortage more interesting, because cheese-free pizzas keep working.
2. **Reservation shape.** One ingredient and a quantity (the proposal), or a map of ingredients (what the Java version did)? This follows from the recipe decision.
3. **Sync or async orders.** Does `POST /orders` block until the order is READY or FAILED, or does it return `RECEIVED` at once while the viewer polls `GET /orders/{id}`? For a show, returning at once lets tickets visibly move through CHECKING and COOKING. The proposal's `201 {orderId, status, traceId}` fits either.
4. **Where the lines live.** Does Kitchen return its own line next to its answer, or does the viewer pick lines from raw events and state? Think about NFR-5 and about who should know the characters' personalities.
5. **Who the customers are.** What traits do they have, and where are they stored? The autopilot creates them, but does Order need to know?
6. **Tutor level** (section 0).

### Gaps in the proposal itself

1. **No way to request authoritative stock.** FR-10 needs Kitchen to demand it, but `GET /stock?ingredient=` has no parameter for that. Design one.
2. **Timeouts stack.** If every hop uses 2 seconds, Kitchen and Order give up at the same moment. Order can then report `KITCHEN_TIMEOUT` while Kitchen is still working and makes a reservation afterwards. What should the relationship between the two timeouts be? What happens to the orphaned reservation? (Idempotent reservations are part of the answer.)
3. **No failure-reason catalog.** For each failure at each hop, write down what Kitchen returns and what Order stores. Include Inventory 503, Inventory timeout, Kitchen 503 and unknown ingredient.
4. **The trust arithmetic does not produce the G3 demo.** Trust is capped at 100 and one incident costs 25. One incident leaves 75, which is not below 50. It takes three incidents to trigger the authoritative check, but the P1 test expects the second order to behave differently after one incident. Choose a lower starting trust (74 or less), a bigger drop, or a different threshold.
5. **What does "old" mean for stale stock?** Old compared to what, and captured when? A snapshot taken at the moment the flag is switched on is one answer.
6. **No event names or log format.** Define them once and reuse them in all three services. They now also form the viewer's input.
7. **New for the show: the event record.** What fields does an event need so the viewer can pick a line and a movement? Think: timestamp, service, event type, order ID, trace ID, and which character is speaking or being spoken to. Also think about how `?since=` should work so the viewer never misses or repeats an event.
8. **New for the show: who restocks?** If the supplier is too generous, nothing ever fails. If too stingy, everything fails. How do you tune it?

---

## 5. What the Java version already figured out (reference)

You will rebuild Inventory in Python, but the Java version works as a behaviour spec. Nothing from it needs to be copied; the *behaviour* is what matters. Its README is at `~/Desktop/Folders/Programming/Java/PizzaOps/PizzaOps/README.md`, and its 11 tests are in `inventory-service/src/test/.../InventoryHttpIntegrationTests.java`.

| Endpoint | Behaviour |
|---|---|
| `GET /stock?ingredient=` | 200 `{ingredient, available}` or 404 `UNKNOWN_INGREDIENT` |
| `POST /admin/restock {ingredient, quantity}` | Adds stock and creates the ingredient if new. 409 `STOCK_LIMIT_EXCEEDED` on integer overflow |
| `POST /reservations {orderId (UUID), items: {ingredient: qty}}` | 200 `RESERVED`. A repeat of the same order and items returns the original and deducts nothing. Same order ID with different items gives 409 `IDEMPOTENCY_CONFLICT`. A shortage gives 409 `INSUFFICIENT_STOCK` and nothing is deducted. An unknown ingredient gives 404 |
| any bad input | 400 `INVALID_REQUEST` |

**How its `reserve()` worked.** Learn the idea; Python replaces the database tools with a lock.

1. Claim the order ID. Simultaneous duplicate requests must be serialized here.
2. If it already existed, return the original reservation. If the items differ, answer 409 `IDEMPOTENCY_CONFLICT`.
3. Lock the stock for the requested ingredients. (Java did this in alphabetical order to prevent deadlocks.)
4. Check every ingredient before deducting any.
5. Deduct. If anything failed, nothing is kept, including the order-ID claim, so a retry after restocking works.

**Scenarios to re-create as pytest tests** (the Java tests covered these): a repeated reservation; same order ID with different items; reserving more than exists; one real and one unknown ingredient (is anything deducted?); malformed input; 12 parallel requests competing for 3 units of cheese; simultaneous duplicate requests.

**Requirement scorecard (all of it restarts in Python):**

| Requirement | Java version | Python version |
|---|---|---|
| FR-1 to FR-6 (Order) | Missing | To do |
| FR-7 to FR-12 (Kitchen, mood) | Missing | To do |
| FR-13 `GET /stock` | Partial | To do |
| FR-14 atomic reserve + 409 | Done | To do |
| FR-15 idempotent reservations | Done | To do |
| FR-16 stale stock, FR-17 delay and 503 | Missing | To do |
| FR-18 health | Inventory only | To do |
| FR-19 structured log line per event | Not met | To do |
| FR-20 reconcile | Missing | To do |
| NFR-1 to NFR-9 | Mostly missing | To do |

---

## 6. Next steps

Do these in order. For each step: what you will learn, the questions to answer yourself, and how to know it is done. Bring me your answers and your code, and I will review them.

### Step 0: Set up the environment

*Learn:* what a virtual environment is, and why several Pythons on one Mac need one.

In the project folder, run `uv init --bare --python 3.13`, then `uv add "fastapi[standard]" httpx pytest pygame-ce`. This creates `pyproject.toml`, a `.venv` and `uv.lock`. Add `.venv/` to `.gitignore`, and run `git init` if you want version control.

*Done when:* a file containing a single FastAPI route, started with `uv run fastapi dev` (or `uv run uvicorn`), answers `curl http://127.0.0.1:8082/health`. Delete it afterwards or keep it as the seed of Inventory.

### Step 1: Write the contract sheet (no code)

*Learn:* contract-first design. Most distributed bugs live between services, not inside them.

Create `CONTRACTS.md`. Keep it to one or two pages:

- The exact request and response for `POST /cook` and `POST /orders`.
- The reservation shape and where recipes live.
- The authoritative-stock parameter (gap 1).
- The failure-reason table (gap 3).
- The timeout budget with its inner-versus-outer rule (gap 2).
- Sync or async (decision 3).
- The starting trust value (gap 4).
- The log format and event names, and the event record the viewer needs (gaps 6 and 7).

*Done when:* someone could write Kitchen using only that sheet.

### Step 2: Build Inventory in Python (port 8082)

*Learn:* a web endpoint, request validation, status codes, and why check-then-deduct needs a lock.

Build the behaviours in section 5 with in-memory dicts. Questions to answer first:

1. FastAPI runs a plain `def` endpoint on a worker thread and an `async def` endpoint on one event loop. What happens to 12 simultaneous reservations in each case, and why do you still want a lock? Test it.
2. How do you make "check every ingredient, then deduct all" atomic across two ingredients?
3. A single lock around the whole reservation is acceptable at this scale. Why? When would it stop being acceptable?
4. What happens when two identical requests arrive at the same moment?

*Done when:* the scenarios in section 5 pass as pytest tests, and `curl` shows the contract's fields.

### Step 3: Build Kitchen (port 8081)

*Learn:* calling another service with `httpx`, timeouts, and translating errors between layers.

- `POST /cook` looks up the recipe, calls Inventory, and maps each outcome to your failure table.
- No mood yet.
- Put the Inventory URL and the timeout in configuration, not in the code.

Questions to answer first: What should Kitchen do when Inventory is down? When it is slow? Where is the line between a Kitchen error and an Inventory error in the response?

*Done when* (all with curl, no Order needed):

- With stock, `POST /cook` returns 200 `COOKED` and the stock drops.
- With too little stock, you get 409 `INSUFFICIENT_STOCK` in the standard error shape.
- With Inventory stopped, you get a clean error within the timeout. There is no hang and no raw stack trace.
- The same trace ID appears in both logs.

### Step 4: Build Order (port 8080) and reach P0

*Learn:* state machines, owning the truth, and why "READY" must be earned.

- Implement `POST /orders`, `GET /orders/{id}` and `GET /orders`, validation, trace ID creation and the call to Kitchen.
- Use a status type that can only hold the five states. Store orders in a thread-safe in-memory structure.
- Map every Kitchen outcome to a `failureReason` (your table from Step 1).
- Never report READY unless Kitchen said COOKED.

*Done when* (milestone P0):

- One `curl POST /orders` ends in READY with a trace ID.
- `grep` for that trace ID finds lines in all three logs.
- A shortage produces FAILED with `INSUFFICIENT_STOCK`.
- Stopping Kitchen produces FAILED with a sensible reason.
- `/health` is UP on all three ports.

### Step 5: P0 acceptance tests

Write the first two rows of the proposal's test table as pytest tests: normal path, and insufficient stock. The "dependency timeout" test needs the delay fault (FR-17), so it waits for P2.

### Step 6: Events

*Learn:* one record serving three purposes (log, feed, speech).

Add the event record and `GET /events?since=` to all three services, in one agreed format.

*Done when:* an order produces a readable, time-ordered set of events across the three services.

### Step 7: Viewer v0 (rectangles and speech bubbles)

*Learn:* a game loop, polling without freezing the window, and mapping events to behaviour.

A pygame window with coloured rectangles for the waiter, chef and stockkeeper, and a customer who appears per order. Speech bubbles show authored lines. Start with plain text and no art.

*Done when:* you can run an order with curl and see the characters react.

### Step 8: P1, the first argument

Stale-stock flag, Kitchen mood and trust as plain unit-tested Python, `RELATIONSHIP_CHANGED` events, and authored lines per mood. The stockkeeper lies, the chef loses trust, and the next order behaves differently on screen.

### Step 9: Autopilot

Customer generator, director and supplier, all seeded. Now the show runs without you. Add pause, speed and seed selection to the viewer if you want them.

### Step 10: P2, more chaos, then polish

Delay and 503 modes, `/admin/faults`, retry limit with idempotent reservations, reconcile, cooldowns. Then replace rectangles with sprites and add walking paths.

The proposal says to stop after P1 and decide. A version that makes you laugh counts as finished.

**The Later list** (do not start before P1 works): an LLM for dialogue, a browser version of the viewer, Godot or Unity, Docker, a message queue.

---

## 7. Self-check for G6

You are done learning each topic when you can answer these without looking:

- Why does a repeated `POST /reservations` not deduct stock twice, and what stops two simultaneous duplicates?
- What does 409 mean, and how is it different from 400, 404 and 503?
- Why is a timeout different from an error response, and what must a caller assume after one?
- Why must the outer timeout be longer than the inner one?
- Why would a lock be needed even in a single Python process, and what would break if two reservations took their locks in opposite order?
- What does "stale" mean here, and why must reservations ignore it?
- Why does the log line carry both an order ID and a trace ID?
- What is fault injection, and why is it safe here (seeded, explicit, never corrupting real stock)?
- Why does the viewer only read? What would break if it could change the world?
