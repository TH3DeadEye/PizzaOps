package ca.milani.arman.pizzaops.inventoryservice.model;

import jakarta.persistence.CheckConstraint;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Table(name = "inventory_stock", check = @CheckConstraint(constraint = "available >= 0"))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class StockItem {
    @Id
    @Column(length = 40)
    private String ingredient;

    @Column(nullable = false)
    private int available;

    public void add(int quantity) {
        if (quantity <= 0) {
            throw new IllegalArgumentException("Restock quantity must be positive");
        }
        available = Math.addExact(available, quantity);
    }

    public void reserve(int quantity) {
        if (quantity <= 0 || quantity > available) {
            throw new IllegalArgumentException("Invalid reservation quantity");
        }
        available -= quantity;
    }
}
