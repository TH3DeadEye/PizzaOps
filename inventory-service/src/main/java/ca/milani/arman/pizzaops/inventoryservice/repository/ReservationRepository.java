package ca.milani.arman.pizzaops.inventoryservice.repository;

import ca.milani.arman.pizzaops.inventoryservice.model.Reservation;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.UUID;

public interface ReservationRepository extends JpaRepository<Reservation, UUID> {
    // PostgreSQL waits for a concurrent insert of the same orderId to finish.
    // The stock change and this insert share one transaction, so they commit together.
    @Modifying
    @Query(value = """
            INSERT INTO inventory_reservations (order_id, reservation_id, trace_id, created_at)
            VALUES (:orderId, :reservationId, :traceId, CURRENT_TIMESTAMP)
            ON CONFLICT (order_id) DO NOTHING
            """, nativeQuery = true)
    int insertIfAbsent(@Param("orderId") UUID orderId,
                       @Param("reservationId") UUID reservationId,
                       @Param("traceId") String traceId);
}
