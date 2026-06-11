from __future__ import annotations

from dagploy_dax.manager.client import get_hatchet
from dagploy_dax.manager.utils import get_worker_name, get_worker_slots
from dagploy_dax.manager.task import HANDLERS, dax_workflow


def main() -> None:
    worker_name = get_worker_name()
    slots = get_worker_slots()

    print(f"Registered handlers: {sorted(HANDLERS.keys())}")
    print(f"Starting worker name={worker_name} slots={slots}")

    worker = get_hatchet().worker(
        name=worker_name,
        slots=slots,
        workflows=[dax_workflow],
    )

    worker.start()


if __name__ == "__main__":
    main()