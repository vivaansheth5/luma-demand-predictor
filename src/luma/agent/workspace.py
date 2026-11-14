"""The agent's working state: everything it knows on the as-of date, and its draft plan."""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from luma.config import Settings
from luma.data.panel import Panel
from luma.inventory.decide import InventoryPlan
from luma.models.gbm import DemandForecaster
from luma.models.pipeline import Forecast
from luma.models.sizecurve import SizeCurves, fit_size_curves
from luma.models.uncensor import Uncensored
from luma.money import money
from luma.signals.anomaly import Anomaly, detect_anomalies
from luma.signals.trends import TrendSignal, detect_trends
from luma.sim.catalog import distance_matrix


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
    def can_transfer(self) -> bool:
        """Transfers need at least two stores with known coordinates."""
        return self.panel.n_stores > 1 and self.panel.has_map

    def money(self, amount: float, compact: bool = True) -> str:
        return money(amount, self.panel.currency, compact)

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

    def save(self, path: Path) -> None:
        """Save the trained state so questions and re-plans need no retraining."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(self, fh)

    @staticmethod
    def load(path: Path) -> Workspace:
        """Load a workspace written by :meth:`save`. Only open files you created yourself."""
        with path.open("rb") as fh:
            return pickle.load(fh)

    def sku_index(self, sku_id: str) -> int:
        """Resolve a product by id, case-insensitively."""
        wanted = sku_id.strip().lower()
        for i, value in enumerate(self.panel.catalog["sku_id"]):
            if str(value).lower() == wanted:
                return i
        raise KeyError(f"unknown product id {sku_id!r}")


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
        distance=distance_matrix(panel.stores) if panel.has_map else np.zeros((panel.n_stores, panel.n_stores)),
    )
