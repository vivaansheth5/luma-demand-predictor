"""The agent's working state: everything it knows on the as-of date, and its draft plan."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from demand_agent.config import Settings
from demand_agent.data.panel import Panel
from demand_agent.inventory.decide import InventoryPlan
from demand_agent.models.gbm import DemandForecaster
from demand_agent.models.pipeline import Forecast
from demand_agent.models.sizecurve import SizeCurves, fit_size_curves
from demand_agent.models.uncensor import Uncensored
from demand_agent.signals.anomaly import Anomaly, detect_anomalies
from demand_agent.signals.trends import TrendSignal, detect_trends
from demand_agent.sim.catalog import distance_matrix


@dataclass
class Workspace:
    settings: Settings
    panel: Panel
    uncensored: Uncensored
    model: DemandForecaster
    forecast: Forecast
    size_curves: SizeCurves
    trends: list[TrendSignal]
    anomalies: list[Anomaly]
    distance: np.ndarray
    draft: InventoryPlan | None = None
    committed: dict | None = None
    notes: dict = field(default_factory=dict)

    @property
    def store_names(self) -> list[str]:
        return list(self.panel.stores["name"])

    def store_index(self, name: str) -> int:
        """Resolve a store by name or id, case-insensitively."""
        wanted = name.strip().lower()
        for i, row in self.panel.stores.iterrows():
            if wanted in (str(row["name"]).lower(), str(row["store_id"]).lower()):
                return int(i)
        raise KeyError(f"unknown store {name!r}; stores are {self.store_names}")

    def sku_index(self, sku_id: str) -> int:
        ids = list(self.panel.catalog["sku_id"])
        try:
            return ids.index(sku_id.strip().upper())
        except ValueError:
            raise KeyError(f"unknown product id {sku_id!r}") from None


def build_workspace(
    panel: Panel,
    settings: Settings,
    uncensored: Uncensored,
    model: DemandForecaster,
    forecast: Forecast,
) -> Workspace:
    """Run the signal detectors and size curves and collect everything the agent needs."""
    demand = uncensored.by_sku
    return Workspace(
        settings=settings,
        panel=panel,
        uncensored=uncensored,
        model=model,
        forecast=forecast,
        size_curves=fit_size_curves(panel),
        trends=detect_trends(panel, demand),
        anomalies=detect_anomalies(panel, demand),
        distance=distance_matrix(panel.stores),
    )
