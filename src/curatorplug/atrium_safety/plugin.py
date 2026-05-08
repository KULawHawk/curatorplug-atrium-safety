"""The Pluggy hookimpl class for atrium-safety.

This is the integration boundary that ties the decision logic
(:mod:`curatorplug.atrium_safety.enforcer`) to Curator's plugin
runtime. Curator discovers this class via the setuptools entry point:

    [project.entry-points.curator]
    atrium_safety = "curatorplug.atrium_safety.plugin:AtriumSafetyPlugin"

(That mapping lives in ``pyproject.toml``.)

Strict-mode configuration: the plugin reads the environment variable
``CURATORPLUG_ATRIUM_SAFETY_STRICT`` at class instantiation. Setting
it to ``1`` / ``true`` / ``yes`` (case-insensitive) enables strict
mode. The default (unset, or any other value) is non-strict.

Why an env var instead of a config file: the plugin is auto-discovered
at Curator startup before user config is loaded. An env var is the
simplest way to influence that early-startup behavior without
restructuring Curator's plugin discovery.

Future: a proper config integration (read from ``curator.toml``
``[plugins.atrium_safety]`` section) is a v0.2.0 candidate.
"""

from __future__ import annotations

import os

from loguru import logger

# We import @hookimpl from Curator's plugin namespace -- this is the
# decorator that marks methods as Pluggy hook implementations bound to
# the "curator" project namespace (created in
# curator.plugins.hookspecs via pluggy.HookspecMarker("curator")).
from curator.plugins import hookimpl

from curatorplug.atrium_safety.enforcer import decide, enforce


def _read_strict_mode_env() -> bool:
    """Parse the ``CURATORPLUG_ATRIUM_SAFETY_STRICT`` env var.

    Returns True iff the value (case-insensitive) is one of
    ``{'1', 'true', 'yes', 'on'}``. Any other value (including
    unset, empty, '0', 'false', etc.) returns False.
    """
    raw = os.environ.get("CURATORPLUG_ATRIUM_SAFETY_STRICT", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class AtriumSafetyPlugin:
    """Curator plugin enforcing Atrium Constitution Principle 2.

    Auto-instantiated by Curator's plugin manager at runtime via the
    setuptools entry point. Users normally never instantiate this
    directly.

    Args:
        strict_mode: when True, refuse any write where verification was
            skipped at the source side. When False (default), the
            plugin is essentially a no-op -- it observes
            ``curator_source_write_post`` events but does not refuse
            any of them. Defaults are read from
            ``CURATORPLUG_ATRIUM_SAFETY_STRICT`` at construction.
    """

    def __init__(self, strict_mode: bool | None = None) -> None:
        if strict_mode is None:
            strict_mode = _read_strict_mode_env()
        self.strict_mode = strict_mode
        logger.debug(
            "AtriumSafetyPlugin: initialized with strict_mode={s}",
            s=self.strict_mode,
        )

    @hookimpl
    def curator_source_write_post(
        self,
        source_id: str,
        file_id: str,
        src_xxhash: str | None,
        written_bytes_len: int,
    ) -> None:
        """Hookimpl for Curator's ``curator_source_write_post`` (v1.1.1+).

        Decides whether the write is compliant per Atrium Principle 2,
        and either:

        * Logs a debug message (compliant, OR non-strict + skipped
          verify) -- the operation continues normally.
        * Raises :class:`ComplianceError` (strict + skipped verify) --
          MigrationService's outer exception-boundary catches this and
          marks the move ``MigrationOutcome.FAILED`` with the
          exception's message in ``MigrationMove.error``.

        See ``enforcer.py`` for the decision policy.
        """
        decision = decide(
            source_id=source_id,
            file_id=file_id,
            src_xxhash=src_xxhash,
            written_bytes_len=written_bytes_len,
            strict_mode=self.strict_mode,
        )
        # Always log the decision for debugability. ``loguru`` filters
        # debug messages out by default in production; users can opt in
        # via ``LOGURU_LEVEL=DEBUG``.
        logger.debug(
            "AtriumSafetyPlugin: source_id={sid}, verdict={v}, msg={m}",
            sid=source_id, v=decision.verdict.value, m=decision.message,
        )
        # Enforce raises on REFUSE; no-op otherwise.
        enforce(decision)


# ---------------------------------------------------------------------------
# Module-level instance for setuptools entry-point auto-discovery.
# ---------------------------------------------------------------------------
#
# Pluggy's ``load_setuptools_entrypoints`` calls ``ep.load()`` and
# registers the result as a plugin. For an entry point of the form
# ``module:Class``, ``ep.load()`` returns the CLASS, and pluggy registers
# that class object directly -- no auto-instantiation. The result is
# that pluggy invokes hookimpls as unbound methods, ``self`` doesn't
# get auto-bound, and pluggy's argument-filling collapses by one slot
# (the hookimpl appears to be missing its last positional argument).
#
# To make pluggy register an INSTANCE instead, the entry point must
# point at an actual instance defined at module level. That is what
# ``plugin`` below is for. ``pyproject.toml`` declares:
#
#     [project.entry-points.curator]
#     atrium_safety = "curatorplug.atrium_safety.plugin:plugin"
#
# Auto-discovered instances read their config from the
# ``CURATORPLUG_ATRIUM_SAFETY_STRICT`` env var at module-import time.
# Users who want to construct the plugin with explicit kwargs (e.g.
# in tests, or for programmatic registration) can still instantiate
# ``AtriumSafetyPlugin(strict_mode=...)`` directly and register that
# with their own plugin manager.
plugin = AtriumSafetyPlugin()
