from __future__ import annotations
from functools import lru_cache
from hatchet_sdk import Hatchet

from dagploy_dax.manager.utils import load_app_env

@lru_cache(maxsize=1)
def get_hatchet() -> Hatchet:
    load_app_env(required=False)
    return Hatchet()