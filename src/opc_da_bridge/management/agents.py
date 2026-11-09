"""Read-only agent boundary. Only the explicitly simulated adapter exists."""
from __future__ import annotations

import copy
import datetime as dt
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Protocol

from .store import utc_now


class AgentAdapter(Protocol):
    def read(self, variables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ...


class AuthenticatedAgentChannel(Protocol):
    """Future typed integration point, deliberately without an implementation.

    A real channel must authenticate both sides, validate certificates and
    acknowledge an exact validated configuration revision. It is not a generic
    remote shell. No API endpoint invokes this protocol in the laboratory UI.
    """
    def apply_configuration(self, destination_id: str, document: Dict[str, Any],
                            expected_revision: int) -> Dict[str, Any]:
        ...

    def health(self, destination_id: str) -> Dict[str, Any]:
        ...


class SimulatedAgent:
    def __init__(self, clock=utc_now):
        self.clock = clock
        self.available = True
        self.lock = threading.RLock()
        self.cache = OrderedDict()

    def prune(self, valid_ids):
        with self.lock:
            for identifier in list(self.cache):
                if identifier not in valid_ids:
                    del self.cache[identifier]

    def set_available(self, available):
        with self.lock:
            self.available = available
            if not available:
                for _, sample in self.cache.values():
                    sample.update(quality="Bad", quality_code=8)

    def read(self, variables):
        with self.lock:
            output = []
            for variable in variables:
                fingerprint = (variable["item_id"], variable["source_id"],
                               variable["data_type"], variable["initial_value"])
                cached = self.cache.get(variable["id"])
                if cached is None or cached[0] != fingerprint:
                    sample = {"variable_id": variable["id"], "item_id": variable["item_id"],
                              "source_id": variable["source_id"], "data_type": variable["data_type"],
                              "value": None, "quality": "Bad", "quality_code": 32,
                              "source_timestamp": None, "origin": "Agente simulado"}
                    self.cache[variable["id"]] = (fingerprint, sample)
                    if len(self.cache) > 10000:
                        self.cache.popitem(last=False)
                else:
                    sample = cached[1]
                    self.cache.move_to_end(variable["id"])
                if self.available:
                    timestamp = self.clock()
                    try:
                        parsed = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        valid = parsed.tzinfo is not None
                    except (TypeError, ValueError, AttributeError):
                        valid = False
                    sample.update(value=variable["initial_value"],
                                  quality="Good" if valid else "Uncertain",
                                  quality_code=192 if valid else 64,
                                  source_timestamp=timestamp if valid else None)
                else:
                    sample.update(quality="Bad", quality_code=8)
                output.append(copy.deepcopy(sample))
            return output
