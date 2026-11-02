package ca.milani.arman.pizzaops.inventoryservice.dto;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

public record ReservationResponse(
        UUID reservationId,
        UUID orderId,
        String status,
        Map<String, Integer> items,
        String traceId,
        Instant createdAt
) {
}
