"""Simulated OPC DA telemetry source producing samples across canonical types.

Generates realistic process values with source UTC timestamps,
monotonic session sequence tracking, and configurable noise/failures.
"""
from __future__ import annotations

import random
import time
from typing import Any

from opc_da_bridge.core.model import (
    OPC_QUALITY_GOOD,
    ValueType,
    encode_value,
    utc_now_us,
)
from opc_da_bridge.protocol.messages import DataStreamPayload, ItemDataUpdate


class SimulatedSourceTag:
    """Configuration and state for a single simulated tag."""

    def __init__(
        self,
        item_id: int,
        path: str,
        value_type: ValueType,
        base_value: Any,
        noise_pct: float = 0.01,
    ) -> None:
        self.item_id = item_id
        self.path = path
        self.value_type = value_type
        self.base_value = base_value
        self.noise_pct = noise_pct

    def generate_sample_update(self, timestamp_us: int | None = None) -> ItemDataUpdate:
        ts = timestamp_us if timestamp_us is not None else utc_now_us()
        val = self.base_value

        if self.value_type in (ValueType.F64, ValueType.F32):
            noise = random.uniform(-self.noise_pct, self.noise_pct)
            val = float(self.base_value) * (1.0 + noise)
            if self.value_type == ValueType.F32:
                val = round(val, 3)
            else:
                val = round(val, 6)
        elif self.value_type in (ValueType.I32, ValueType.I16):
            delta = random.randint(-2, 2)
            val = int(self.base_value) + delta
        elif self.value_type == ValueType.BOOL:
            val = bool(self.base_value)
        elif self.value_type == ValueType.STRING:
            val = str(self.base_value)

        val_bytes = encode_value(int(self.value_type), val)
        return ItemDataUpdate(
            item_id=self.item_id,
            opc_item_path=self.path,
            status=0,
            value_type=int(self.value_type),
            quality=OPC_QUALITY_GOOD,
            timestamp_us=ts,
            value=val_bytes,
            error_code=0,
        )


class SimulatedSource:
    """Simulated telemetry generator producing versioned DataStreamPayload batches."""

    def __init__(self, session_id: str = "sim_source_session_1", source_id: str = "sim_source_1") -> None:
        self.session_id = session_id
        self.source_id = source_id
        self.sequence = 0
        self.tags: list[SimulatedSourceTag] = []
        self._init_default_tags()

    def _init_default_tags(self) -> None:
        self.tags = [
            SimulatedSourceTag(1, "Simulated.Turbine.Speed", ValueType.F64, 3000.0, 0.005),
            SimulatedSourceTag(2, "Simulated.Furnace.Temperature", ValueType.F32, 850.5, 0.01),
            SimulatedSourceTag(3, "Simulated.Line.Pressure", ValueType.I32, 120, 0.02),
            SimulatedSourceTag(4, "Simulated.Pump.Active", ValueType.BOOL, True),
            SimulatedSourceTag(5, "Simulated.Status.Message", ValueType.STRING, "NORMAL_OPERATION"),
            SimulatedSourceTag(6, "Simulated.Counter.Cycles", ValueType.I16, 1500),
        ]

    def add_tag(self, tag: SimulatedSourceTag) -> None:
        self.tags.append(tag)

    def next_batch(self, force_seq: int | None = None) -> DataStreamPayload:
        """Produce the next DataStreamPayload batch incrementing the sequence number."""
        if force_seq is not None:
            seq = force_seq
        else:
            self.sequence += 1
            seq = self.sequence

        ts = utc_now_us()
        updates = [tag.generate_sample_update(timestamp_us=ts) for tag in self.tags]
        return DataStreamPayload(
            session_id=self.session_id,
            sequence=seq,
            source_id=self.source_id,
            items=updates,
        )
