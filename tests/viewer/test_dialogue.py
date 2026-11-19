from viewer.dialogue import cashier_line, chef_line, customer_line, fill, first_name, stock_worker_line


def test_same_seed_picks_the_same_line_and_a_different_seed_does_not():
    a = chef_line("COOK_STARTED", {}, "CALM", "s1", pizza="PEPPERONI")
    b = chef_line("COOK_STARTED", {}, "CALM", "s1", pizza="PEPPERONI")
    others = {chef_line("COOK_STARTED", {}, "CALM", f"s{n}", pizza="PEPPERONI") for n in range(20)}
    assert a == b and a
    assert len(others) > 1
    assert "pepperoni" in fill("a {pizza}", pizza="PEPPERONI")


def test_failed_orders_apologise_and_ready_orders_celebrate():
    ready = customer_line("ORDER_READY", "patient", "x", pizza="CHEESE")
    failed = customer_line("ORDER_FAILED", "patient", "x", pizza="CHEESE")
    cashier_sorry = cashier_line("ORDER_FAILED", {"failureReason": "INSUFFICIENT_STOCK",
                                                 "lastError": {"details": {"jobError": {"details": {"missing": {"CHEESE": 2}}}}}},
                                 "patient", "x", pizza="CHEESE")
    assert "thank" in ready.lower() or "wait" in ready.lower() or "lovely" in ready.lower()
    assert "shame" in failed.lower() or "never" in failed.lower() or "happen" in failed.lower()
    assert "cheese" in cashier_sorry.lower()
    assert customer_line("ORDER_READY", "patient", "x") != customer_line("ORDER_FAILED", "patient", "x")


def test_chef_accuses_on_a_relationship_change_and_mentions_the_missing_ingredient():
    lines = {chef_line("RELATIONSHIP_CHANGED",
                       {"missing": {"CHEESE": 2}, "claimed": {"CHEESE": 30, "DOUGH": 40}, "reason": "STALE_STOCK_CONTRADICTION"},
                       "FURIOUS", f"seed-{n}") for n in range(20)}
    assert any("cheese" in line.lower() for line in lines)
    assert any("lie" in line.lower() or "clipboard" in line.lower() or "stale" in line.lower() for line in lines)


def test_stock_worker_distinguishes_cached_from_fresh_and_faults():
    cached = stock_worker_line("STOCK_READ", {"source": "CACHED"}, "a")
    fresh = stock_worker_line("STOCK_READ", {"source": "AUTHORITATIVE"}, "a")
    stale = stock_worker_line("FAULT_CONFIG_CHANGED", {"faults": {"staleStockEnabled": True, "delayMs": 0, "failureMode": "NONE"}}, "a")
    assert cached != fresh
    assert "memory" in stale.lower() or "sheet" in stale.lower() or "count" in stale.lower()


def test_unknown_events_are_silent_and_fill_is_forgiving():
    assert chef_line("SERVICE_STARTED", {}, "CALM", "x") is None
    assert customer_line("COOK_STARTED", "patient", "x") is None
    assert "pizza" in fill("a {pizza}") and "..." in fill("missing {nope}")
    assert first_name({"PEPPERONI": 2, "DOUGH": 1}) == "pepperoni"
    assert first_name({}) == "ingredients"


def _templates(table):
    if isinstance(table, str):
        yield table
    elif isinstance(table, dict):
        for value in table.values():
            yield from _templates(value)
    else:
        for value in table:
            yield from _templates(value)


def test_stock_worker_names_the_ingredient_that_actually_ran_out():
    payload = {"code": "INSUFFICIENT_STOCK", "items": {"DOUGH": 3, "CHEESE": 3, "PEPPERONI": 3}, "missing": {"PEPPERONI": 1}}
    lines = {stock_worker_line("RESERVATION_REJECTED", payload, f"s{n}") for n in range(12)}
    assert all("pepperoni" in line.lower() and "dough" not in line.lower() for line in lines)
    # Recordings made before ``missing`` existed still produce a line.
    assert stock_worker_line("RESERVATION_REJECTED", {"items": {"CHEESE": 2}}, "old")


def test_no_line_can_show_an_order_id_and_customers_get_stable_names():
    from viewer import dialogue
    for table in (dialogue.CHEF, dialogue.STOCK_WORKER, dialogue.CASHIER, dialogue.CUSTOMER):
        assert not [text for text in _templates(table) if "{short_order}" in text]
    lines = {stock_worker_line("RESERVATION_SUCCEEDED", {}, f"s{n}", name="Robin", pizza="CHEESE", short_order="5f613e14")
             for n in range(20)}
    assert not any("5f613e14" in line for line in lines) and any("Robin" in line for line in lines)
    customer = {"id": "0a6c2d7e-1b1f-4c55-9f19-3b7d2a1e9c11", "appearance": "customer_teal"}
    assert dialogue.customer_name(customer) == dialogue.customer_name(dict(customer)) in dialogue.NAMES
    assert len({dialogue.customer_name({"id": f"id-{n}"}) for n in range(40)}) > 5
    assert dialogue.customer_name(None) is None
