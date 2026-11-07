from services.inventory.errors import InventoryError
from services.inventory.models import DelayPhase, FailureMode


async def before_operation(config, sleep):
    if config.delay_phase == DelayPhase.BEFORE_MUTATION and config.delay_ms:
        await sleep(config.delay_ms / 1000)
    if config.failure_mode == FailureMode.UNAVAILABLE:
        raise InventoryError(503, "INVENTORY_UNAVAILABLE", "Inventory is unavailable in this scenario",
                             {"phase": "BEFORE_MUTATION"})


async def after_operation(config, sleep):
    if config.delay_phase == DelayPhase.AFTER_MUTATION_BEFORE_RESPONSE and config.delay_ms:
        await sleep(config.delay_ms / 1000)
