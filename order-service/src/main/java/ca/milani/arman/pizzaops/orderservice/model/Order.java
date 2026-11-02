package ca.milani.arman.pizzaops.orderservice.model;

public record Order(
        String id,
        String customerId,
        String pizzaId,
        String size,
        String status
){
}
