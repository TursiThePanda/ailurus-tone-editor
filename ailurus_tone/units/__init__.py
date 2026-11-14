"""Every supported unit. To add one, write its module next to these and list its class here
(docs/adding-a-unit.md walks through it). The order is the preference for the first active unit
and the order of the unit menu."""
from __future__ import annotations

from .gx10 import GX10
from .katana3 import Katana

UNITS = [GX10, Katana]
BY_KIND = {u.kind: u for u in UNITS}
