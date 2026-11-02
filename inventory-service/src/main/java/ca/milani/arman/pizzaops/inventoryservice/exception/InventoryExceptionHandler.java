package ca.milani.arman.pizzaops.inventoryservice.exception;

import ca.milani.arman.pizzaops.inventoryservice.web.TraceIdFilter;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class InventoryExceptionHandler {
    public record ApiError(String code, String message, String traceId) {
    }

    @ExceptionHandler(InventoryException.class)
    ResponseEntity<ApiError> inventoryError(InventoryException exception, HttpServletRequest request) {
        return ResponseEntity.status(exception.getStatus()).body(
                new ApiError(exception.getCode(), exception.getMessage(), traceId(request)));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ApiError> validationError(MethodArgumentNotValidException exception,
                                              HttpServletRequest request) {
        String message = exception.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getField() + ": " + error.getDefaultMessage())
                .findFirst().orElse("Invalid request");
        return ResponseEntity.badRequest().body(new ApiError("INVALID_REQUEST", message, traceId(request)));
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MissingServletRequestParameterException.class})
    ResponseEntity<ApiError> malformedRequest(Exception exception, HttpServletRequest request) {
        return ResponseEntity.badRequest().body(
                new ApiError("INVALID_REQUEST", "Check the request JSON and required parameters", traceId(request)));
    }

    private String traceId(HttpServletRequest request) {
        return (String) request.getAttribute(TraceIdFilter.ATTRIBUTE);
    }
}
