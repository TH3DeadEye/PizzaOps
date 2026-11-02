# PizzaOps

A local autonomous restaurant simulation built with separate Spring Boot services.
Java 17, Spring Boot 4.1.2, and Gradle match the existing project.

| Application | Port | Current state |
| --- | --- | --- |
| InventoryService | 8082 | Stock, restocking, atomic ingredient reservations |
| KitchenService | 8081 | Next milestone |
| OrderService | 8080 | Existing starter and Order record; HTTP workflow still to build |

The root Gradle project groups the services; it does not run a fourth application.
The original root `src/` demo is retained as an inactive scaffold. Your OrderService
Java files are unchanged. Run Gradle commands below from this directory.

## Run InventoryService

Start Docker Desktop, then:

```sh
docker compose up -d --wait inventory-db
./gradlew :inventory-service:bootRun
```

The database uses localhost port 55432 to avoid taking over an existing database on
5432. Inventory starts on `http://localhost:8082` and binds only to localhost.
The Compose username and password are `pizzaops` and `pizzaops-local` for this local
simulation. To use your own PostgreSQL database, create a database owned by your
Inventory user and set `DB_URL`, `DB_USERNAME`, and `DB_PASSWORD` before starting it.
Only Inventory accesses this database; Kitchen and Order will call its HTTP API.

The app uses `ddl-auto=update` to create tables for this first local version. Stock
starts empty and persists in the Compose volume across restarts. Add migrations
before treating the schema as stable. No ingredients are replenished at startup.

Check health:

```sh
curl http://localhost:8082/actuator/health
```

## Try the first workflow

Add stock. A restock creates an ingredient if it does not exist:

```sh
curl -sS -X POST http://localhost:8082/admin/restock \
  -H 'Content-Type: application/json' \
  -d '{"ingredient":"cheese","quantity":10}'

curl -sS -X POST http://localhost:8082/admin/restock \
  -H 'Content-Type: application/json' \
  -d '{"ingredient":"dough","quantity":10}'
```

Reserve ingredients for an order:

```sh
curl -sS -X POST http://localhost:8082/reservations \
  -H 'Content-Type: application/json' \
  -H 'X-Trace-Id: pizza-001' \
  -d '{"orderId":"00000000-0000-0000-0000-000000000001","items":{"cheese":2,"dough":1}}'
```

Repeat that exact reservation. It returns the original reservation and does not
deduct stock again. Cheese stays at 8 and dough at 9:

```sh
curl -sS 'http://localhost:8082/stock?ingredient=cheese'
```

Use a new UUID for a new order. Reusing an order ID with different items returns
`409 IDEMPOTENCY_CONFLICT`. A shortage returns `409 INSUFFICIENT_STOCK` and rolls
back the whole reservation. Restock and retry the same order after a shortage.
An unknown ingredient returns `404 UNKNOWN_INGREDIENT`. Invalid quantities, names,
or JSON return `400 INVALID_REQUEST`. Ingredient names use lowercase letters,
digits, underscores, or hyphens, start with a letter, and are at most 40 characters.
Quantities must be positive whole numbers within the Java integer range.

Successful reservation responses always use HTTP 200, including retries. Reserving
deducts available stock immediately and records the allocation; release, expiry,
and consumption are later milestones. Restock is an additive admin action, so
repeating a restock adds stock again.

Every response has an `X-Trace-Id` header. Send that header through each service as
we add Kitchen and Order. Missing or invalid trace headers get a generated UUID.
Reservation responses retain the original reservation's trace ID, while the response
header and log messages identify the current request.

## How the code fits together

1. `InventoryController` receives HTTP and validates request DTOs.
2. `InventoryService` makes the stock and reservation decisions inside a transaction.
3. `StockRepository` and `ReservationRepository` access PostgreSQL through JPA.
4. `StockItem` and `Reservation` hold database state. Response DTOs define the API.

`@Transactional` makes a reservation all-or-nothing. PostgreSQL row locks stop two
orders from taking the same last ingredient. Locks are acquired in ingredient order
to avoid opposite lock ordering. A unique order ID plus `INSERT ... ON CONFLICT`
serializes simultaneous retries, so a timeout and retry cannot double-deduct stock.
These guarantees also apply across multiple application instances sharing this
Inventory database.

## Test

These tests use real HTTP and a dedicated PostgreSQL database, including competing
orders, simultaneous retries, simultaneous restocks, rollback, validation, and
ingredient lock ordering. They do not use the simulation database.

```sh
docker compose --profile test up -d --wait inventory-test-db
./gradlew test :inventory-service:bootJar
docker compose --profile test stop inventory-test-db
```

To use an existing test database, set `TEST_DB_URL`, `TEST_DB_USERNAME`, and
`TEST_DB_PASSWORD`. Its database name must end with `_test`; the integration tests
clear the Inventory tables in that database between cases.

## Next milestones

Build Kitchen's HTTP client and a basic cook request against this API, then wire
Order into Kitchen. Once a complete order works, add controlled stale-stock reports,
delays, failures, trust/patience rules, and autonomous customers. Fault injection will
affect responses and timing while preserving the real reservation invariants.
