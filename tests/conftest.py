"""Pytest fixtures for the atrium-safety unit tests.

These fixtures are deliberately Curator-free -- they don't construct
a CuratorRuntime or import from curator core (other than ``hookimpl``).
This keeps the unit tests fast and isolated; the integration tests in
P3 will exercise the real Curator runtime.
"""

from __future__ import annotations

import pytest

from curatorplug.atrium_safety.plugin import AtriumSafetyPlugin


@pytest.fixture
def plugin_strict() -> AtriumSafetyPlugin:
    """An :class:`AtriumSafetyPlugin` with strict mode forced ON."""
    return AtriumSafetyPlugin(strict_mode=True)


@pytest.fixture
def plugin_lax() -> AtriumSafetyPlugin:
    """An :class:`AtriumSafetyPlugin` with strict mode forced OFF."""
    return AtriumSafetyPlugin(strict_mode=False)
