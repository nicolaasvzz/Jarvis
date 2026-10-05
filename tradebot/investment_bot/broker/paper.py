"""Simulated broker: instant fills against the provided reference price."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .base import Broker, ExecutionModel, Fill, Order


@dataclass
class PaperBroker(Broker):
    execution: ExecutionModel = field(default_factory=ExecutionModel)

    def submit(self, order: Order, ref_price: float, timestamp: pd.Timestamp) -> Fill | None:
        if ref_price <= 0:
            return None
        fill = self.execution.simulate(order, ref_price, timestamp)
        self.fills.append(fill)
        return fill
