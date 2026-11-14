"""Shared fixtures: one tiny world and one full pipeline run for the whole test session."""

from __future__ import annotations

import warnings

import pytest

from luma.config import small_settings
from luma.run import run_pipeline
from luma.sim.world import generate_world

warnings.filterwarnings("ignore", category=UserWarning)


@pytest.fixture(scope="session")
def settings():
    return small_settings()


@pytest.fixture(scope="session")
def world(settings):
    return generate_world(settings.sim)


@pytest.fixture(scope="session")
def result(settings, world):
    return run_pipeline(settings, world=world, with_policy=True)
