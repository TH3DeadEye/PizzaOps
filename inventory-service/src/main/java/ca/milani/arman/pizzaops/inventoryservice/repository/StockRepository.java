package ca.milani.arman.pizzaops.inventoryservice.repository;

import ca.milani.arman.pizzaops.inventoryservice.model.StockItem;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface StockRepository extends JpaRepository<StockItem, String> {
    @Modifying
    @Query(value = """
            INSERT INTO inventory_stock (ingredient, available)
            VALUES (:ingredient, 0)
            ON CONFLICT (ingredient) DO NOTHING
            """, nativeQuery = true)
    int insertIfAbsent(@Param("ingredient") String ingredient);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select s from StockItem s where s.ingredient = :ingredient")
    Optional<StockItem> findForUpdate(@Param("ingredient") String ingredient);

    // Every reservation locks in the same order to avoid opposite lock ordering.
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select s from StockItem s where s.ingredient in :ingredients order by s.ingredient")
    List<StockItem> findAllForUpdate(@Param("ingredients") Collection<String> ingredients);
}
