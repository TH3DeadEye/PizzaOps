package ca.milani.arman.pizzaops.inventoryservice;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class InventoryHttpIntegrationTests {
    @LocalServerPort
    private int port;

    @Autowired
    private ObjectMapper json;

    @Autowired
    private JdbcTemplate jdbc;

    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(3)).build();

    @BeforeEach
    void resetTestDatabase() {
        assertThat(jdbc.queryForObject("select current_database()", String.class))
                .as("Use a dedicated database whose name ends with _test")
                .endsWith("_test");
        jdbc.update("delete from inventory_reservation_items");
        jdbc.update("delete from inventory_reservations");
        jdbc.update("delete from inventory_stock");
    }

    @Test
    void reservesAllIngredientsAndReplaysTheOriginalResponse() throws Exception {
        restock("cheese", 10);
        restock("dough", 10);
        Map<String, Object> request = reservation(UUID.randomUUID(), Map.of("cheese", 2, "dough", 1));

        var first = post("/reservations", request, "pizza-001");
        var replay = post("/reservations", request, "pizza-001-retry");

        assertThat(first.statusCode()).isEqualTo(200);
        assertThat(first.headers().firstValue("X-Trace-Id")).contains("pizza-001");
        assertThat(tree(first).path("traceId").asString()).isEqualTo("pizza-001");
        assertThat(tree(first).path("status").asString()).isEqualTo("RESERVED");
        assertThat(replay.statusCode()).isEqualTo(200);
        assertThat(tree(replay)).isEqualTo(tree(first));
        assertThat(available("cheese")).isEqualTo(8);
        assertThat(available("dough")).isEqualTo(9);
        assertThat(reservationCount()).isEqualTo(1);
    }

    @Test
    void rejectsChangingItemsOnAnExistingOrder() throws Exception {
        restock("cheese", 10);
        UUID orderId = UUID.randomUUID();
        assertThat(post("/reservations", reservation(orderId, Map.of("cheese", 2))).statusCode()).isEqualTo(200);

        var conflict = post("/reservations", reservation(orderId, Map.of("cheese", 3)));

        assertThat(conflict.statusCode()).isEqualTo(409);
        assertThat(tree(conflict).path("code").asString()).isEqualTo("IDEMPOTENCY_CONFLICT");
        assertThat(available("cheese")).isEqualTo(8);
    }

    @Test
    void aShortageRollsBackEverythingAndAllowsRetryAfterRestocking() throws Exception {
        restock("cheese", 2);
        restock("dough", 1);
        var request = reservation(UUID.randomUUID(), Map.of("cheese", 2, "dough", 2));

        var failure = post("/reservations", request);

        assertThat(failure.statusCode()).isEqualTo(409);
        assertThat(tree(failure).path("code").asString()).isEqualTo("INSUFFICIENT_STOCK");
        assertThat(available("cheese")).isEqualTo(2);
        assertThat(available("dough")).isEqualTo(1);
        assertThat(reservationCount()).isZero();

        restock("dough", 1);
        assertThat(post("/reservations", request).statusCode()).isEqualTo(200);
        assertThat(available("cheese")).isZero();
        assertThat(available("dough")).isZero();
    }

    @Test
    void anUnknownIngredientDoesNotLeaveAPartialReservation() throws Exception {
        restock("cheese", 5);

        var failure = post("/reservations", reservation(UUID.randomUUID(), Map.of("cheese", 1, "onion", 1)));

        assertThat(failure.statusCode()).isEqualTo(404);
        assertThat(tree(failure).path("code").asString()).isEqualTo("UNKNOWN_INGREDIENT");
        assertThat(available("cheese")).isEqualTo(5);
        assertThat(reservationCount()).isZero();
    }

    @Test
    void validatesTheRequestBeforeChangingStock() throws Exception {
        restock("cheese", 5);
        String orderId = UUID.randomUUID().toString();
        List<String> invalidReservations = List.of(
                "{}",
                "{\"orderId\":\"" + orderId + "\",\"items\":{}}",
                "{\"orderId\":\"" + orderId + "\",\"items\":{\"cheese\":0}}",
                "{\"orderId\":\"" + orderId + "\",\"items\":{\"cheese\":-1}}",
                "{\"orderId\":\"" + orderId + "\",\"items\":{\"cheese\":null}}",
                "{\"orderId\":\"" + orderId + "\",\"items\":{\"cheese\":1.5}}",
                "{\"orderId\":\"" + orderId + "\",\"items\":{\"CHEESE\":1}}",
                "{\"orderId\":\"invalid-uuid\",\"items\":{\"cheese\":1}}"
        );
        for (String body : invalidReservations) {
            var response = send("POST", "/reservations", body, "invalid-request");
            assertThat(response.statusCode()).as(body).isEqualTo(400);
            assertThat(tree(response).path("code").asString()).isEqualTo("INVALID_REQUEST");
        }
        assertThat(post("/admin/restock", Map.of("ingredient", "cheese", "quantity", -2)).statusCode())
                .isEqualTo(400);
        assertThat(available("cheese")).isEqualTo(5);
        assertThat(reservationCount()).isZero();
    }

    @Test
    void simultaneousOrdersCannotOversellTheLastIngredients() throws Exception {
        restock("cheese", 3);

        var results = concurrently(12, () -> post("/reservations",
                reservation(UUID.randomUUID(), Map.of("cheese", 1))));

        assertThat(results.stream().filter(r -> r.statusCode() == 200).count()).isEqualTo(3);
        assertThat(results.stream().filter(r -> r.statusCode() == 409).count()).isEqualTo(9);
        assertThat(available("cheese")).isZero();
        assertThat(reservationCount()).isEqualTo(3);
    }

    @Test
    void simultaneousRetriesDeductStockOnlyOnce() throws Exception {
        restock("cheese", 10);
        var request = reservation(UUID.randomUUID(), Map.of("cheese", 2));

        var results = concurrently(12, () -> post("/reservations", request));

        JsonNode original = tree(results.get(0));
        for (var result : results) {
            assertThat(result.statusCode()).isEqualTo(200);
            assertThat(tree(result)).isEqualTo(original);
        }
        assertThat(available("cheese")).isEqualTo(8);
        assertThat(reservationCount()).isEqualTo(1);
    }

    @Test
    void simultaneousRestocksCreateTheIngredientOnceWithoutLosingUpdates() throws Exception {
        var results = concurrently(12, () -> post("/admin/restock", Map.of("ingredient", "cheese", "quantity", 1)));

        assertThat(results).allSatisfy(result -> assertThat(result.statusCode()).isEqualTo(200));
        assertThat(available("cheese")).isEqualTo(12);
    }

    @Test
    void overlappingReservationsUseConsistentLockOrdering() throws Exception {
        restock("cheese", 10);
        restock("dough", 10);
        var alternatingOrder = new java.util.concurrent.atomic.AtomicInteger();

        var results = concurrently(12, () -> {
            Map<String, Integer> items = new LinkedHashMap<>();
            if (alternatingOrder.getAndIncrement() % 2 == 0) {
                items.put("cheese", 2);
                items.put("dough", 2);
            } else {
                items.put("dough", 2);
                items.put("cheese", 2);
            }
            return post("/reservations", reservation(UUID.randomUUID(), items));
        });

        assertThat(results.stream().filter(r -> r.statusCode() == 200).count()).isEqualTo(5);
        assertThat(results.stream().filter(r -> r.statusCode() == 409).count()).isEqualTo(7);
        assertThat(available("cheese")).isZero();
        assertThat(available("dough")).isZero();
    }

    @Test
    void stockOverflowReturnsAConflictWithoutChangingStock() throws Exception {
        restock("cheese", Integer.MAX_VALUE);

        var failure = post("/admin/restock", Map.of("ingredient", "cheese", "quantity", 1));

        assertThat(failure.statusCode()).isEqualTo(409);
        assertThat(tree(failure).path("code").asString()).isEqualTo("STOCK_LIMIT_EXCEEDED");
        assertThat(available("cheese")).isEqualTo(Integer.MAX_VALUE);
    }

    @Test
    void exposesHealthAndGeneratesATraceId() throws Exception {
        var health = send("GET", "/actuator/health", null, null);

        assertThat(health.statusCode()).isEqualTo(200);
        assertThat(tree(health).path("status").asString()).isEqualTo("UP");
        assertThat(health.headers().firstValue("X-Trace-Id")).isPresent();
        assertThat(send("GET", "/stock", null, null).statusCode()).isEqualTo(400);
    }

    private Map<String, Object> reservation(UUID orderId, Map<String, Integer> items) {
        return Map.of("orderId", orderId, "items", items);
    }

    private void restock(String ingredient, int quantity) throws Exception {
        var response = post("/admin/restock", Map.of("ingredient", ingredient, "quantity", quantity));
        assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
    }

    private int available(String ingredient) throws Exception {
        var response = send("GET", "/stock?ingredient=" + ingredient, null, null);
        assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
        return tree(response).path("available").asInt();
    }

    private long reservationCount() {
        return jdbc.queryForObject("select count(*) from inventory_reservations", Long.class);
    }

    private JsonNode tree(HttpResponse<String> response) {
        return json.readTree(response.body());
    }

    private HttpResponse<String> post(String path, Object body) throws Exception {
        return post(path, body, null);
    }

    private HttpResponse<String> post(String path, Object body, String traceId) throws Exception {
        return send("POST", path, json.writeValueAsString(body), traceId);
    }

    private HttpResponse<String> send(String method, String path, String body, String traceId) throws Exception {
        var request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path))
                .timeout(Duration.ofSeconds(15))
                .header("Content-Type", "application/json");
        if (traceId != null) {
            request.header("X-Trace-Id", traceId);
        }
        request.method(method, body == null ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofString(body));
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }

    private <T> List<T> concurrently(int count, Callable<T> action) throws Exception {
        var executor = Executors.newFixedThreadPool(count);
        var ready = new CountDownLatch(count);
        var start = new CountDownLatch(1);
        try {
            List<Future<T>> futures = new ArrayList<>();
            for (int i = 0; i < count; i++) {
                futures.add(executor.submit(() -> {
                    ready.countDown();
                    if (!start.await(5, TimeUnit.SECONDS)) {
                        throw new IllegalStateException("Timed out waiting for concurrent requests");
                    }
                    return action.call();
                }));
            }
            assertThat(ready.await(5, TimeUnit.SECONDS)).isTrue();
            start.countDown();
            List<T> results = new ArrayList<>();
            for (Future<T> future : futures) {
                results.add(future.get(20, TimeUnit.SECONDS));
            }
            return results;
        } finally {
            start.countDown();
            executor.shutdownNow();
        }
    }
}
