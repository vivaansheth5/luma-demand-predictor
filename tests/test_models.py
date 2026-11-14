from dataclasses import replace

import numpy as np
from scipy.stats import nbinom, poisson

from demand_agent.config import QUANTILES, WINDOWS
from demand_agent.evaluation.backtest import LAST_WEEK, MODEL, ORACLE
from demand_agent.features.build import DRIVER_GROUPS, FeatureBuilder, feature_groups
from demand_agent.models.gbm import CalibrationSet
from demand_agent.models.pipeline import expected_shortage, nb_ppf, nb_sf
from demand_agent.models.sizecurve import fit_concentration, fit_size_curves
from demand_agent.models.uncensor import conditional_mean_at_least, uncensor


def test_conditional_mean_matches_brute_force():
    k = np.arange(200)
    for mu, sold in ((0.3, 0), (0.3, 1), (2.5, 3), (6.0, 2), (1.0, 5)):
        pmf = poisson.pmf(k, mu)
        expected = (k * pmf)[k >= sold].sum() / pmf[k >= sold].sum()
        got = conditional_mean_at_least(np.array([mu]), np.array([sold]))[0]
        assert abs(got - expected) < 1e-4


def test_uncensoring_only_changes_censored_cells_and_reduces_bias(world):
    panel, truth = world.panel, world.truth
    unc = uncensor(panel)
    sales = panel.sales.astype(float)
    assert np.array_equal(unc.demand[~panel.censored], sales[~panel.censored])
    assert (unc.demand >= sales - 1e-6).all()
    assert (unc.draw >= sales).all()
    true_total = truth.demand[: panel.n_days].sum()
    assert abs(unc.demand.sum() - true_total) < abs(sales.sum() - true_total)


def test_features_do_not_read_the_future(world):
    """Features at an origin must not change when later data changes."""
    panel = world.panel
    unc = uncensor(panel).by_sku
    origin = panel.n_days - 40
    base = FeatureBuilder(panel, unc).build([origin], with_target=False)

    tampered = unc.copy()
    tampered[origin + 1 :] *= 7.0
    sales, eod, buzz = panel.sales.copy(), panel.eod.copy(), panel.buzz.copy()
    sales[origin + 1 :] = 0
    eod[origin + 1 :] = 0
    buzz[origin + 1 :] = 999.0
    shifted = replace(panel, sales=sales, eod=eod, buzz=buzz)
    other = FeatureBuilder(shifted, tampered).build([origin], with_target=False)
    assert np.allclose(base.X.to_numpy(dtype=float), other.X.to_numpy(dtype=float), equal_nan=True)


def test_target_is_the_window_total(world):
    panel = world.panel
    unc = uncensor(panel).by_sku
    builder = FeatureBuilder(panel, unc)
    origin = builder.labelled_origins(7, 56)[-1]
    frame = builder.build([origin])
    i = 5
    start, length = WINDOWS[frame.window[i]]
    total = unc[origin + start : origin + start + length, frame.store[i], frame.sku[i]].sum()
    assert np.isclose(frame.y_units[i], total, rtol=1e-5)


def test_every_feature_belongs_to_a_driver_group(result):
    names, groups = feature_groups(result.rolling.model.feature_names)
    assert names == list(DRIVER_GROUPS)
    assert len(groups) == len(result.rolling.model.feature_names)


def test_forecast_quantiles_are_ordered_and_non_negative(result):
    fc = result.rolling.live.forecast
    q = fc.quantiles[fc.active]
    assert np.isfinite(q).all() and (q >= 0).all()
    assert (np.diff(q, axis=-1) >= -1e-9).all()
    cumulative = [WINDOWS.index((1, n)) for n in (7, 14, 21, 28)]
    assert (np.diff(fc.mean[fc.active][:, cumulative], axis=1) >= -1e-9).all()


def test_forecast_is_between_naive_and_oracle(result):
    overall = result.evaluation["overall"]
    assert overall[ORACLE]["wape"] < overall[MODEL]["wape"] < overall[LAST_WEEK]["wape"]
    assert abs(overall[MODEL]["bias"]) < 0.15


def test_live_forecast_is_calibrated_from_earlier_errors(result):
    assert result.rolling.live.calibrated
    assert set(result.rolling.model.offsets) == {7, 14, 21, 28}
    assert not result.rolling.folds[0].calibrated


def test_calibration_set_recovers_a_known_spread():
    rng = np.random.default_rng(1)
    n = 20000
    mean = np.full(n, 8.0)
    actual = mean + rng.normal(0, 3.0, n) * 1.0
    raw = np.repeat(mean[:, None], len(QUANTILES), axis=1)  # every "quantile" sits at the mean
    cal = CalibrationSet()
    cal.add(np.full(n, 7.0), actual, mean, raw)
    shifted = raw + cal.offsets()[7][None, :] * np.sqrt(mean + 1.0)[:, None]
    coverage = (actual[:, None] <= shifted).mean(axis=0)
    assert np.allclose(coverage, QUANTILES, atol=0.01)


def test_negative_binomial_helpers():
    mean, shape = np.array([4.0]), np.array([2.5])
    p = shape / (shape + mean)
    assert np.isclose(nb_sf(np.array([3]), mean, shape)[0], nbinom.sf(3, shape[0], p[0]))
    assert nb_ppf(0.9, mean, shape)[0] == nbinom.ppf(0.9, shape[0], p[0])
    k = np.arange(400)
    brute = (np.maximum(k - 3, 0) * nbinom.pmf(k, shape[0], p[0])).sum()
    assert abs(expected_shortage(np.array([3]), mean, shape, support=300)[0] - brute) < 1e-3


def test_thinning_keeps_the_shape():
    """A negative binomial split by a size share keeps its shape; this is what the size model relies on."""
    rng = np.random.default_rng(2)
    mean, shape, share = 12.0, 3.0, 0.3
    total = rng.negative_binomial(shape, shape / (shape + mean), 200_000)
    part = rng.binomial(total, share)
    for k in (0, 2, 5, 9):
        assert abs((part > k).mean() - nb_sf(np.array([k]), np.array([share * mean]), np.array([shape]))[0]) < 0.006


def test_size_curves_pool_towards_truth(world):
    curves = fit_size_curves(world.panel)
    assert np.allclose(curves.share.sum(axis=-1), 1.0)
    active = world.panel.active_at(world.panel.n_days - 1)
    raw = np.where(np.isfinite(curves.raw), curves.raw, 0.25)
    truth = world.truth.size_share
    assert np.abs(curves.share - truth)[active].mean() < np.abs(raw - truth)[active].mean()


def test_concentration_is_recovered():
    rng = np.random.default_rng(3)
    prior = np.tile(np.array([0.2, 0.3, 0.3, 0.2]), (400, 1))
    p = rng.dirichlet(80.0 * prior[0], 400)
    counts = np.stack([rng.multinomial(60, row) for row in p]).astype(float)
    assert 45 < fit_concentration(counts, prior) < 140
