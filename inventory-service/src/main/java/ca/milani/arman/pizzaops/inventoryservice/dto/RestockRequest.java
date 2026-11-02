package ca.milani.arman.pizzaops.inventoryservice.dto;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;

public record RestockRequest(
        @NotNull @Pattern(regexp = "[a-z][a-z0-9_-]{0,39}") String ingredient,
        @NotNull @Positive Integer quantity
) {
}
