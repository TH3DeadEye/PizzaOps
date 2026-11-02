package ca.milani.arman.pizzaops.inventoryservice.exception;

import lombok.Getter;
import org.springframework.http.HttpStatus;

@Getter
public class InventoryException extends RuntimeException {
    private final HttpStatus status;
    private final String code;

    public InventoryException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }
}
