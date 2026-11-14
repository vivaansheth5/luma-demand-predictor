"""Dark stores and product catalogue of the synthetic world."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from luma.config import CATEGORIES, SimSettings


@dataclass(frozen=True)
class StoreSpec:
    store_id: str
    name: str
    lat: float
    lon: float
    open_day: int  # days after the world start
    scale: float  # demand scale of the catchment
    trend_delay: int  # days this neighbourhood lags the city on style trends
    price_sensitivity: float
    affinity: tuple[float, float, float, float, float]  # by category, in CATEGORIES order
    size_shift: float  # positive means the catchment buys larger sizes
    weekend_lift: float


STORES: tuple[StoreSpec, ...] = (
    StoreSpec("IND", "Indiranagar", 12.9719, 77.6412, 0, 1.25, 0, 0.80, (1.00, 1.15, 0.90, 1.00, 1.30), -0.10, 1.35),
    StoreSpec("KOR", "Koramangala", 12.9352, 77.6245, 0, 1.40, 0, 1.00, (1.20, 1.00, 1.15, 1.10, 1.05), -0.15, 1.25),
    StoreSpec("HSR", "HSR Layout", 12.9116, 77.6474, 91, 1.05, 4, 1.00, (1.10, 1.05, 1.10, 1.10, 0.90), 0.00, 1.20),
    StoreSpec("JAY", "Jayanagar", 12.9308, 77.5838, 189, 0.80, 12, 1.25, (1.00, 0.85, 0.90, 0.95, 1.10), 0.10, 1.15),
    StoreSpec("JPN", "JP Nagar", 12.9063, 77.5857, 203, 0.70, 10, 1.20, (1.05, 0.90, 1.00, 1.00, 0.95), 0.15, 1.15),
    StoreSpec("BEL", "Bellandur", 12.9304, 77.6784, 280, 0.95, 5, 0.90, (0.95, 1.20, 1.05, 1.10, 0.85), 0.20, 1.10),
    StoreSpec("WFD", "Whitefield", 12.9698, 77.7500, 343, 0.90, 7, 0.75, (0.90, 1.30, 0.95, 1.05, 1.00), 0.25, 1.10),
    StoreSpec("HEB", "Hebbal", 13.0358, 77.5970, 455, 0.65, 8, 1.00, (1.00, 1.05, 1.00, 0.95, 1.00), 0.10, 1.15),
)

STYLES: tuple[str, ...] = (
    "minimal",
    "graphic-pop",
    "grunge",
    "blokecore",
    "retro-sport",
    "gorpcore",
    "y2k",
    "coquette",
)

COLORS: tuple[str, ...] = ("black", "white", "earth", "maroon", "blue", "pastel", "neon")

LABELS: tuple[str, ...] = ("KERB", "STATIC", "OFFSIDE", "DRIFT", "NAMMA", "HALFDAY")

# Per category: base daily units at a scale-1 store, price range, subtypes with fits, style mix.
_CATEGORY_SPEC: dict[str, dict] = {
    "tees": {
        "base": 1.15,
        "price": (799, 1299),
        "subtypes": (("Graphic Tee", ("oversized", "regular", "boxy")),),
        "styles": {
            "graphic-pop": 0.30,
            "minimal": 0.22,
            "grunge": 0.18,
            "blokecore": 0.12,
            "y2k": 0.10,
            "retro-sport": 0.08,
        },
        "size_curve": (0.17, 0.33, 0.32, 0.18),
        "code": "TEE",
    },
    "outerwear": {
        "base": 0.80,
        "price": (1799, 3299),
        "subtypes": (
            ("Hoodie", ("oversized", "regular")),
            ("Zip Hoodie", ("oversized", "regular")),
            ("Coach Jacket", ("regular", "boxy")),
            ("Fleece", ("regular", "oversized")),
        ),
        "styles": {"gorpcore": 0.30, "minimal": 0.25, "grunge": 0.20, "graphic-pop": 0.15, "retro-sport": 0.10},
        "size_curve": (0.15, 0.32, 0.34, 0.19),
        "code": "OUT",
    },
    "jerseys": {
        "base": 0.85,
        "price": (1199, 1799),
        "subtypes": (("Football Jersey", ("regular", "oversized")),),
        "styles": {"blokecore": 0.60, "retro-sport": 0.40},
        "size_curve": (0.14, 0.34, 0.34, 0.18),
        "code": "JER",
    },
    "pants": {
        "base": 0.90,
        "price": (1499, 2499),
        "subtypes": (
            ("Baggy Jeans", ("baggy",)),
            ("Cargo Pants", ("baggy", "relaxed")),
            ("Sweatpants", ("relaxed", "regular")),
        ),
        "styles": {"y2k": 0.28, "gorpcore": 0.24, "minimal": 0.22, "grunge": 0.16, "retro-sport": 0.10},
        "size_curve": (0.22, 0.34, 0.28, 0.16),
        "code": "PNT",
    },
    "womens_tops": {
        "base": 0.95,
        "price": (799, 1599),
        "subtypes": (
            ("Baby Tee", ("cropped", "regular")),
            ("Crop Top", ("cropped",)),
            ("Corset Top", ("cropped", "regular")),
        ),
        "styles": {"coquette": 0.32, "y2k": 0.30, "minimal": 0.22, "grunge": 0.16},
        "size_curve": (0.30, 0.36, 0.22, 0.12),
        "code": "WMN",
    },
}

_FIT_SIZE_SHIFT = {"oversized": -0.22, "boxy": -0.10, "baggy": -0.08, "relaxed": 0.0, "regular": 0.0, "cropped": -0.12}


def _product_name(label: str, fit: str, subtype: str, color: str) -> str:
    """Readable product name; the fit is dropped when the product type already says it."""
    lead = "" if fit[:4] in subtype.lower() else f"{fit.capitalize()} "
    return f"{label} {lead}{subtype}, {color.capitalize()}"


def stores_frame(settings: SimSettings) -> pd.DataFrame:
    """Store master data for the first ``n_stores`` stores."""
    rows = []
    for spec in STORES[: settings.n_stores]:
        rows.append(
            {
                "store_id": spec.store_id,
                "name": spec.name,
                "lat": spec.lat,
                "lon": spec.lon,
                "open_day": min(spec.open_day, max(settings.history_days - 100, 0)),
            }
        )
    return pd.DataFrame(rows)


def build_catalog(settings: SimSettings, rng: np.random.Generator) -> pd.DataFrame:
    """Generate the product catalogue with latent demand parameters.

    Columns prefixed with ``_`` are simulator internals (true popularity, lifecycle, elasticity)
    and are never shown to the forecaster.
    """
    rows: list[dict] = []
    last_launch = settings.history_days - 12
    for category, count in settings.sku_counts:
        spec = _CATEGORY_SPEC[category]
        style_names = list(spec["styles"])
        style_p = np.array(list(spec["styles"].values()))
        style_p = style_p / style_p.sum()
        for i in range(count):
            subtype, fits = spec["subtypes"][rng.integers(len(spec["subtypes"]))]
            fit = fits[rng.integers(len(fits))]
            style = style_names[rng.choice(len(style_names), p=style_p)]
            color_p = np.array([0.30, 0.16, 0.16, 0.10, 0.12, 0.10, 0.06])
            if style == "coquette":
                color_p = np.array([0.10, 0.24, 0.06, 0.12, 0.08, 0.38, 0.02])
            elif style == "gorpcore":
                color_p = np.array([0.24, 0.06, 0.42, 0.06, 0.14, 0.02, 0.06])
            color = COLORS[rng.choice(len(COLORS), p=color_p)]
            label = LABELS[rng.integers(len(LABELS))]
            lo, hi = spec["price"]
            price = float(int(round(rng.uniform(lo, hi) / 100.0)) * 100 - 1)
            core = bool(rng.random() < 0.35)
            # Just over half the range is live at the start; the rest arrives in later drops.
            if rng.random() < 0.55:
                launch = int(rng.integers(-70, 1))
            else:
                launch = int(rng.integers(20, max(last_launch, 21)))
            plateau = int(rng.integers(30, 100))
            half_life = float(rng.uniform(420, 800) if core else rng.uniform(110, 260))
            markdown_age = int(rng.integers(210, 330))
            rows.append(
                {
                    "sku_id": f"{spec['code']}-{i + 1:03d}",
                    "name": _product_name(label, fit, subtype, color),
                    "category": category,
                    "subtype": subtype,
                    "style": style,
                    "color": color,
                    "fit": fit,
                    "label": label,
                    "price": price,
                    "unit_cost": float(round(price * rng.uniform(0.34, 0.44))),
                    "launch_day": launch,
                    "_base": float(spec["base"] * rng.lognormal(-0.18, 0.60)),
                    "_core": core,
                    "_plateau": plateau,
                    "_half_life": half_life,
                    "_elasticity": float(rng.uniform(1.2, 2.4)),
                    "_markdown_day": int(launch + markdown_age) if not core else 10**6,
                    "_markdown_depth": float(rng.choice([0.2, 0.25, 0.3])),
                    "_fit_shift": _FIT_SIZE_SHIFT[fit],
                }
            )
    catalog = pd.DataFrame(rows)
    catalog["category"] = pd.Categorical(catalog["category"], categories=list(CATEGORIES))
    return catalog.sort_values(["category", "sku_id"]).reset_index(drop=True)


def category_size_curves() -> np.ndarray:
    """Base size curve per category, shape ``[n_categories, n_sizes]``."""
    return np.array([_CATEGORY_SPEC[c]["size_curve"] for c in CATEGORIES])


def haversine_km(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def distance_matrix(stores: pd.DataFrame) -> np.ndarray:
    """Pairwise road-distance estimate between stores in kilometres (haversine x 1.35)."""
    lat = stores["lat"].to_numpy()
    lon = stores["lon"].to_numpy()
    return 1.35 * haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
