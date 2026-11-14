import numpy as np

from luma.signals.anomaly import detect_anomalies, expected_series, robust_z
from luma.signals.trends import benjamini_hochberg, detect_trends, mann_kendall, theil_sen


def test_mann_kendall_detects_monotone_series():
    _, p_up = mann_kendall(np.arange(8.0))
    _, p_flat = mann_kendall(np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]))
    assert p_up < 0.01 < p_flat
    s_down, _ = mann_kendall(np.arange(8.0)[::-1])
    assert s_down == -28


def test_theil_sen_ignores_one_outlier():
    x = np.arange(10.0) * 2.0
    x[4] = 100.0
    assert abs(theil_sen(x) - 2.0) < 1e-9


def test_benjamini_hochberg_is_monotone_and_never_below_p():
    p = np.array([0.001, 0.04, 0.03, 0.2, 0.5])
    q = benjamini_hochberg(p)
    assert (q >= p - 1e-12).all() and (q <= 1).all()
    assert (np.diff(q[np.argsort(p)]) >= -1e-12).all()


def test_robust_z_flags_an_injected_spike():
    rng = np.random.default_rng(0)
    y = rng.poisson(40, (120, 3)).astype(float)
    y[100, 1] = 160
    z = robust_z(y, expected_series(y))
    assert z[100, 1] > 6
    assert np.nanmax(np.abs(np.delete(z[60:], 40, axis=0))) < 5


def test_detectors_run_on_the_world(world, result):
    demand = result.rolling.live.uncensored.by_sku
    trends = detect_trends(world.panel, demand)
    assert trends and all(t.status in {"Emerging", "Rising", "Peaking", "Fading", "Stable"} for t in trends)
    assert all(0 <= t.q_value <= 1 for t in trends)
    anomalies = detect_anomalies(world.panel, demand)
    assert all(a.scope in {"city", "store", "product"} for a in anomalies)
    assert all(a.explained_by is None for a in anomalies if a.scope == "store")
