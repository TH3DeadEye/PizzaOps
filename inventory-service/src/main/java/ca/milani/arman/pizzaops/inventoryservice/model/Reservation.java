package ca.milani.arman.pizzaops.inventoryservice.model;

import jakarta.persistence.CollectionTable;
import jakarta.persistence.Column;
import jakarta.persistence.ElementCollection;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.MapKeyColumn;
import jakarta.persistence.Table;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "inventory_reservations")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Reservation {
    // One successful ingredient reservation per order, even when Kitchen retries.
    @Id
    private UUID orderId;

    @Column(nullable = false, unique = true)
    private UUID reservationId;

    @Column(nullable = false, length = 128)
    private String traceId;

    @Column(nullable = false)
    private Instant createdAt;

    @ElementCollection
    @CollectionTable(name = "inventory_reservation_items",
            joinColumns = @JoinColumn(name = "order_id"))
    @MapKeyColumn(name = "ingredient", length = 40)
    @Column(name = "quantity", nullable = false)
    private Map<String, Integer> items = new HashMap<>();

    public void recordItems(Map<String, Integer> requestedItems) {
        items.putAll(requestedItems);
    }
}
