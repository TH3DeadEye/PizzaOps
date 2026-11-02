package ca.milani.arman.pizzaops.inventoryservice.service;

import ca.milani.arman.pizzaops.inventoryservice.dto.ReservationRequest;
import ca.milani.arman.pizzaops.inventoryservice.dto.ReservationResponse;
import ca.milani.arman.pizzaops.inventoryservice.dto.RestockRequest;
import ca.milani.arman.pizzaops.inventoryservice.dto.StockResponse;
import ca.milani.arman.pizzaops.inventoryservice.exception.InventoryException;
import ca.milani.arman.pizzaops.inventoryservice.model.Reservation;
import ca.milani.arman.pizzaops.inventoryservice.model.StockItem;
import ca.milani.arman.pizzaops.inventoryservice.repository.ReservationRepository;
import ca.milani.arman.pizzaops.inventoryservice.repository.StockRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

import static org.springframework.http.HttpStatus.CONFLICT;
import static org.springframework.http.HttpStatus.NOT_FOUND;

@Service
@RequiredArgsConstructor
@Slf4j
public class InventoryService {
    private final StockRepository stockRepository;
    private final ReservationRepository reservationRepository;

    @Transactional(readOnly = true)
    public StockResponse stock(String ingredient) {
        return toResponse(stockRepository.findById(ingredient)
                .orElseThrow(() -> unknownIngredient(ingredient)));
    }

    @Transactional
    public StockResponse restock(RestockRequest request) {
        stockRepository.insertIfAbsent(request.ingredient());
        StockItem item = stockRepository.findForUpdate(request.ingredient()).orElseThrow();
        try {
            item.add(request.quantity());
        } catch (ArithmeticException exception) {
            throw new InventoryException(CONFLICT, "STOCK_LIMIT_EXCEEDED",
                    "Restock would exceed the maximum stock quantity");
        }
        log.info("Restocked {} by {}; available={}", request.ingredient(), request.quantity(), item.getAvailable());
        return toResponse(item);
    }

    @Transactional
    public ReservationResponse reserve(ReservationRequest request, String traceId) {
        boolean created = reservationRepository.insertIfAbsent(
                request.orderId(), UUID.randomUUID(), traceId) == 1;
        Reservation reservation = reservationRepository.findById(request.orderId()).orElseThrow();

        if (!created) {
            if (!reservation.getItems().equals(request.items())) {
                throw new InventoryException(CONFLICT, "IDEMPOTENCY_CONFLICT",
                        "This orderId already has a reservation with different items");
            }
            log.info("Replayed reservation for order {}", request.orderId());
            return toResponse(reservation);
        }

        var lockedItems = stockRepository.findAllForUpdate(request.items().keySet());
        var byIngredient = lockedItems.stream()
                .collect(Collectors.toMap(StockItem::getIngredient, item -> item));

        // Check the whole pizza before deducting any ingredient.
        for (var entry : request.items().entrySet()) {
            StockItem item = byIngredient.get(entry.getKey());
            if (item == null) {
                throw unknownIngredient(entry.getKey());
            }
            if (item.getAvailable() < entry.getValue()) {
                throw new InventoryException(CONFLICT, "INSUFFICIENT_STOCK",
                        "Not enough " + entry.getKey() + "; available=" + item.getAvailable());
            }
        }

        lockedItems.forEach(item -> item.reserve(request.items().get(item.getIngredient())));
        reservation.recordItems(request.items());
        log.info("Reserved {} for order {}", request.items(), request.orderId());
        return toResponse(reservation);
    }

    private InventoryException unknownIngredient(String ingredient) {
        return new InventoryException(NOT_FOUND, "UNKNOWN_INGREDIENT", "Unknown ingredient: " + ingredient);
    }

    private StockResponse toResponse(StockItem item) {
        return new StockResponse(item.getIngredient(), item.getAvailable());
    }

    private ReservationResponse toResponse(Reservation reservation) {
        return new ReservationResponse(reservation.getReservationId(), reservation.getOrderId(),
                "RESERVED", Map.copyOf(reservation.getItems()), reservation.getTraceId(), reservation.getCreatedAt());
    }
}
