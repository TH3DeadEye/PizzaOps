package ca.milani.arman.pizzaops.inventoryservice.dto;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;

import java.util.Map;
import java.util.UUID;

public record ReservationRequest(
        @NotNull UUID orderId,
        @NotEmpty Map<
                @NotNull @Pattern(regexp = "[a-z][a-z0-9_-]{0,39}") String,
                @NotNull @Positive Integer> items
) {
}
