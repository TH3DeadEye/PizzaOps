package ca.milani.arman.pizzaops.inventoryservice.controller;

import ca.milani.arman.pizzaops.inventoryservice.dto.ReservationRequest;
import ca.milani.arman.pizzaops.inventoryservice.dto.ReservationResponse;
import ca.milani.arman.pizzaops.inventoryservice.dto.RestockRequest;
import ca.milani.arman.pizzaops.inventoryservice.dto.StockResponse;
import ca.milani.arman.pizzaops.inventoryservice.service.InventoryService;
import ca.milani.arman.pizzaops.inventoryservice.web.TraceIdFilter;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequiredArgsConstructor
public class InventoryController {
    private final InventoryService inventoryService;

    @GetMapping("/stock")
    public StockResponse stock(@RequestParam String ingredient) {
        return inventoryService.stock(ingredient);
    }

    @PostMapping("/admin/restock")
    public StockResponse restock(@Valid @RequestBody RestockRequest request) {
        return inventoryService.restock(request);
    }

    @PostMapping("/reservations")
    public ReservationResponse reserve(@Valid @RequestBody ReservationRequest request,
                                       @RequestAttribute(TraceIdFilter.ATTRIBUTE) String traceId) {
        return inventoryService.reserve(request, traceId);
    }
}
