"""The Pluggy hookimpl class for atrium-safety.

This is the integration boundary that ties the decision logic
(:mod:`curatorplug.atrium_safety.enforcer`) to Curator's plugin
runtime. Curator discovers this class via the setuptools entry point:

    [project.entry-points.curator]
    atrium_safety = "curatorplug.atrium_safety.plugin:plugin"

(That mapping lives in ``pyproject.toml``.)

Strict-mode configuration: the plugin reads the environment variable
``CURATORPLUG_ATRIUM_SAFETY_STRICT`` at class instantiation. Setting
it to ``1`` / ``true`` / ``yes`` (case-insensitive) enables strict
mode. The default (unset, or any other value) is non-strict.

Why an env var instead of a config file: the plugin is auto-discovered
at Curator startup before user config is loaded. An env var is the
simplest way to influence that early-startup behavior without
restructuring Curator's plugin discovery.

Re-read verification (v0.2.0+): when Curator >= 1.1.2 is installed,
the plugin receives a plugin manager reference via the
``curator_plugin_init`` hookspec. With the pm in hand, the plugin can
INDEPENDENTLY re-read the destination via ``curator_source_read_bytes``
after a successful write and verify the hash itself. This catches
non-deterministic source-plugin bugs that Curator's own verify
(performed before the post-write hook fires) might miss. Without pm
access (older Curator, or pm not provided), the plugin gracefully
degrades to v0.1.0 behavior (strict-mode-refusal-only on skipped
verify).

Future: a proper config integration (read from ``curator.toml``
``[plugins.atrium_safety]`` section) is a v0.3.0+ candidate.
"""

from __future__ import annotations

import os

from loguru import logger

# We import @hookimpl from Curator's plugin namespace -- this is the
# decorator that marks methods as Pluggy hook implementations bound to
# the "curator" project namespace (created in
# curator.plugins.hookspecs via pluggy.HookspecMarker("curator")).
from curator.plugins import hookimpl

from curatorplug.atrium_safety.enforcer import (
    EnforcementDecision,
    EnforcementVerdict,
    decide,
    enforce,
)
from curatorplug.atrium_safety.exceptions import ComplianceError
from curatorplug.atrium_safety.verifier import compute_xxh3


# Chunk size for re-reading dst bytes via curator_source_read_bytes.
# Matches Curator's hash_pipeline default (64KB).
_RE_READ_CHUNK_SIZE = 64 * 1024


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
            skipped at the source side, AND refuse any independent
            re-read mismatch (when pm is available). When False
            (default), the plugin is essentially a no-op -- it observes
            ``curator_source_write_post`` events but does not refuse
            any of them; re-read mismatches are logged at WARN level
            but allow the migration to proceed (advisory mode).
            Defaults are read from ``CURATORPLUG_ATRIUM_SAFETY_STRICT``
            at construction.
    """

    def __init__(self, strict_mode: bool | None = None) -> None:
        if strict_mode is None:
            strict_mode = _read_strict_mode_env()
        self.strict_mode = strict_mode
        # pm reference, populated by curator_plugin_init hookimpl when
        # Curator >= 1.1.2 is installed. None when running against
        # older Curator -- the plugin gracefully degrades to v0.1.0
        # behavior (refusal-only on skipped verify, no re-read).
        self.pm = None
        logger.debug(
            "AtriumSafetyPlugin: initialized with strict_mode={s}",
            s=self.strict_mode,
        )

    @hookimpl
    def curator_plugin_init(self, pm) -> None:
        """Save the plugin manager reference for later use (Curator v1.1.2+).

        Called once per pm at the end of ``_create_plugin_manager``,
        after all plugins are registered. The pm is needed in
        :meth:`curator_source_write_post` to call
        ``curator_source_read_bytes`` for independent re-read
        verification.

        See ``Curator/docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md`` v0.2 for
        the design that motivated this hook, and ``self._verify_via_re_read``
        below for how the saved pm is used.

        Note: this hookimpl is silently ignored when running against
        Curator < 1.1.2 (which doesn't fire this hook); ``self.pm``
        stays at its constructor default of None and the plugin
        degrades to v0.1.0 behavior automatically.
        """
        self.pm = pm
        logger.debug(
            "AtriumSafetyPlugin: received pm reference via curator_plugin_init"
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

        Two-phase enforcement:

        1. **Decide-phase:** call :func:`decide` to evaluate the basic
           compliance rules (negative bytes_len, strict + no
           src_xxhash). Raise ``ComplianceError`` via :func:`enforce`
           if the verdict is REFUSE.

        2. **Re-read-phase (v0.2.0+):** if ``self.pm is not None`` AND
           ``src_xxhash is not None``, perform an INDEPENDENT verify by
           re-reading the dst bytes via ``curator_source_read_bytes``
           and recomputing the hash. Raise ``ComplianceError`` if the
           re-read hash differs from ``src_xxhash`` (in strict mode);
           log at WARN level and allow the migration to proceed in
           lax mode.

        See ``enforcer.py`` for the decide-phase policy, and
        ``self._verify_via_re_read`` for the re-read-phase mechanics.
        """
        # Phase 1: existing decide() logic
        decision = decide(
            source_id=source_id,
            file_id=file_id,
            src_xxhash=src_xxhash,
            written_bytes_len=written_bytes_len,
            strict_mode=self.strict_mode,
        )
        logger.debug(
            "AtriumSafetyPlugin: decide-phase verdict={v}, msg={m}",
            v=decision.verdict.value, m=decision.message,
        )
        # Enforce raises on REFUSE; no-op otherwise.
        enforce(decision)

        # Phase 2: re-read verification (v0.2.0+).
        # Only attempt if we have pm access (Curator >= 1.1.2 fired
        # curator_plugin_init for us) AND we have an expected hash to
        # compare against (caller didn't skip verify).
        if self.pm is not None and src_xxhash is not None:
            re_read_decision = self._verify_via_re_read(
                source_id=source_id,
                file_id=file_id,
                expected_xxhash=src_xxhash,
            )
            logger.debug(
                "AtriumSafetyPlugin: re-read-phase verdict={v}, msg={m}",
                v=re_read_decision.verdict.value, m=re_read_decision.message,
            )
            enforce(re_read_decision)

    def _verify_via_re_read(
        self,
        *,
        source_id: str,
        file_id: str,
        expected_xxhash: str,
    ) -> EnforcementDecision:
        """Re-read dst bytes via the plugin manager and verify hash.

        Calls ``curator_source_read_bytes`` repeatedly to assemble the
        full dst content, computes its xxh3_128, and compares to
        ``expected_xxhash``.

        Returns:
            * ``OK`` if the re-read hash matches (the write is
              independently verified).
            * ``OK`` (with informational message) if the re-read can't
              be performed for non-compliance reasons (no plugin owns
              the source_id, exception during read). Conservative: we
              don't refuse a write just because we couldn't verify it
              ourselves; that's what Curator's own verify is for.
            * ``REFUSE`` (strict mode) if the re-read hash differs from
              ``expected_xxhash``. The message names BOTH hashes so the
              user can see exactly what was expected vs. observed.
            * ``WARN`` (lax mode) if the re-read hash differs but
              strict mode is off. Logged but doesn't refuse.
        """
        try:
            dst_bytes = self._read_all_via_hook(source_id, file_id)
        except Exception as e:  # noqa: BLE001 -- defensive; log + skip verify
            return EnforcementDecision(
                verdict=EnforcementVerdict.OK,
                message=(
                    f"re-read attempt raised {type(e).__name__}: {e}; "
                    "skipping independent verify (conservative -- do not refuse "
                    "a write just because re-read itself failed)."
                ),
            )

        if dst_bytes is None:
            # No plugin handled this source_id. Shouldn't normally
            # happen because the write succeeded for it, but be
            # defensive: treat as "can't verify, don't refuse".
            return EnforcementDecision(
                verdict=EnforcementVerdict.OK,
                message=(
                    f"re-read: no plugin handled curator_source_read_bytes "
                    f"for source_id={source_id!r}; skipping independent verify."
                ),
            )

        actual_xxhash = compute_xxh3(dst_bytes)
        if actual_xxhash.lower() == expected_xxhash.lower():
            return EnforcementDecision(
                verdict=EnforcementVerdict.OK,
                message=(
                    f"re-read verified: dst xxh3={actual_xxhash[:12]}\u2026 matches "
                    f"src xxh3={expected_xxhash[:12]}\u2026; Constitution "
                    "Principle 2 honored (defense-in-depth confirmed)."
                ),
            )

        # Mismatch: refuse if strict, warn if lax
        mismatch_msg = (
            f"compliance: independent re-read verification FAILED. "
            f"expected xxh3={expected_xxhash}, actual xxh3={actual_xxhash}. "
            "This indicates the source plugin returned different bytes on a "
            "subsequent read than it did on the migration's verify read -- "
            "likely a non-deterministic plugin bug, transient I/O issue, "
            "or (worst case) a misbehaving plugin. Atrium Constitution "
            "Principle 2 (Hash-Verify-Before-Move) requires consistent "
            "verification."
        )
        if self.strict_mode:
            return EnforcementDecision(
                verdict=EnforcementVerdict.REFUSE,
                message=mismatch_msg + " Refusing the write.",
            )
        return EnforcementDecision(
            verdict=EnforcementVerdict.WARN,
            message=mismatch_msg + " (lax mode; not refusing.)",
        )

    def _read_all_via_hook(
        self,
        source_id: str,
        file_id: str,
    ) -> bytes | None:
        """Read a file's complete bytes via ``curator_source_read_bytes``.

        Loops in ``_RE_READ_CHUNK_SIZE`` chunks until EOF (None or
        empty bytes returned, OR a short read indicating the last
        chunk). Returns ``None`` if no plugin owned the source_id
        (the very first chunk request returned None).

        Mirrors the chunk-loop pattern in
        ``MigrationService._read_bytes_via_hook`` so the safety plugin's
        re-read uses the same protocol Curator's own verify does.
        """
        chunks: list[bytes] = []
        offset = 0
        while True:
            results = self.pm.hook.curator_source_read_bytes(
                source_id=source_id,
                file_id=file_id,
                offset=offset,
                length=_RE_READ_CHUNK_SIZE,
            )
            # Pluggy returns a list of all plugins' results. For source
            # hooks, exactly one plugin should match; collapse to that
            # one. (Defensive about non-list returns in case pluggy's
            # behavior differs in some configuration.)
            if isinstance(results, list):
                chunk = next((r for r in results if r is not None), None)
            else:
                chunk = results

            if chunk is None:
                if offset == 0:
                    return None  # no plugin owned this source_id
                break  # plugin signaled EOF
            if not chunk:
                break  # empty bytes = EOF
            chunks.append(chunk)
            if len(chunk) < _RE_READ_CHUNK_SIZE:
                break  # short read = last chunk
            offset += len(chunk)
        return b"".join(chunks)


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
