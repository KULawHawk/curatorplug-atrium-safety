"""Integration tests against a real Curator runtime.

These tests exercise the full end-to-end flow:

  1. ``build_runtime`` constructs a CuratorRuntime, including its
     pluggy plugin manager.
  2. The plugin manager calls ``load_setuptools_entrypoints("curator")``,
     which auto-discovers this plugin via the entry point declared in
     ``pyproject.toml``.
  3. A cross-source migration runs through ``MigrationService``, which
     fires ``curator_source_write_post`` after each successful write.
  4. The plugin's hookimpl is invoked and either approves (default lax,
     or compliant write) or refuses (strict mode + skipped verify).

These are SLOWER tests than the unit tests (each one builds a fresh
sqlite database and a real CuratorRuntime), but they prove the plugin
actually works inside Curator -- not just in a fresh PluginManager.

Required for these tests to run:

  * Curator >= 1.1.1 must be importable (i.e., ``pip install -e`` of
    Curator has been done in the active Python environment).
  * This plugin must be installed as a package with its setuptools
    entry point exposed (``pip install -e .`` of this repo).

If either is missing, the import-level fixture imports will fail and
pytest will skip / error out the entire module.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import xxhash

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.migration import MigrationOutcome
from curator.services.safety import SafetyLevel, SafetyReport

from curatorplug.atrium_safety.plugin import AtriumSafetyPlugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_real_file(rt, source_id, path, content=b"some bytes\n"):
    """Create a real file on disk + index it under ``source_id``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    h = xxhash.xxh3_128(content).hexdigest()
    e = FileEntity(
        curator_id=uuid4(),
        source_id=source_id,
        source_path=str(path),
        size=len(content),
        mtime=datetime.fromtimestamp(path.stat().st_mtime),
        extension=path.suffix.lower(),
        xxhash3_128=h,
    )
    rt.file_repo.upsert(e)
    return e


def _get_atrium_safety_plugin(rt) -> AtriumSafetyPlugin:
    """Find the auto-registered atrium-safety plugin instance.

    Asserts that the plugin was actually discovered. The setuptools
    entry-point machinery should have registered it during
    ``build_runtime``; if it's not there, something is wrong with the
    installation (e.g., the plugin wasn't ``pip install``-ed and
    therefore has no entry-point metadata).
    """
    for name, plugin in rt.pm.list_name_plugin():
        if name == "atrium_safety":
            assert isinstance(plugin, AtriumSafetyPlugin), (
                f"Expected AtriumSafetyPlugin but got {type(plugin).__name__} "
                f"-- is the entry point pointing at the class instead of the "
                f"module-level instance?"
            )
            return plugin
    raise AssertionError(
        "atrium-safety plugin was NOT auto-registered. "
        "Plugins registered: "
        f"{[n for n, _ in rt.pm.list_name_plugin()]}. "
        "Check that this package is installed via pip (with the entry point "
        "exposed) -- editable installs work too."
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_with_plugin(tmp_path):
    """Real CuratorRuntime with auto-discovered plugin + two local sources.

    Uses the same two-local-source-IDs strategy as Curator's own
    cross-source migration tests: ``local`` and ``local:vault`` are both
    handled by Curator's built-in LocalPlugin, but they're DIFFERENT
    source_ids, so the migration goes through the cross-source code
    path (which fires ``curator_source_write_post``).
    """
    db_path = tmp_path / "integration.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    for sid, name in [
        ("local", "Local Primary"),
        ("local:vault", "Local Vault"),
    ]:
        try:
            rt.source_repo.insert(SourceConfig(
                source_id=sid, source_type="local", display_name=name,
            ))
        except Exception:
            pass

    # SafetyService stub: tmp_path is under %LOCALAPPDATA% on Windows,
    # which real safety would flag as CAUTION. Force everything SAFE so
    # the migration mechanics can be tested without false positives.
    def _safe(self, path, **kw):
        return SafetyReport(path=path, level=SafetyLevel.SAFE)
    with patch.object(rt.safety.__class__, "check_path", _safe):
        yield rt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPluginAutoDiscovery:
    """The plugin should be automatically registered by build_runtime
    via setuptools entry points -- no manual registration required.
    """

    def test_plugin_is_registered_in_runtime(self, runtime_with_plugin):
        # Should NOT raise
        plugin = _get_atrium_safety_plugin(runtime_with_plugin)
        assert plugin is not None

    def test_registered_plugin_is_instance_not_class(self, runtime_with_plugin):
        # This is the bug that was caught and fixed during P2: pluggy
        # registers what ``ep.load()`` returns directly. If the entry
        # point points at the class, the class itself is registered
        # (pluggy does NOT auto-instantiate). We pinned the entry point
        # at the module-level instance to fix this; this test ensures
        # that fix doesn't regress.
        plugin = _get_atrium_safety_plugin(runtime_with_plugin)
        # If the entry point regressed back to pointing at the class,
        # ``plugin`` here would actually BE the class object (a ``type``)
        # and this assertion would fail.
        assert isinstance(plugin, AtriumSafetyPlugin)
        # Plugin should have its strict_mode attribute set (constructor
        # ran -- it's an instance, not a class):
        assert hasattr(plugin, "strict_mode")
        assert plugin.strict_mode in (True, False)


class TestLaxModeDoesNotInterfere:
    """Default mode is lax. Cross-source migrations should run normally
    with the plugin auto-discovered -- no refusals, no errors."""

    def test_normal_migration_succeeds_in_lax(self, runtime_with_plugin, tmp_path):
        rt = runtime_with_plugin
        plugin = _get_atrium_safety_plugin(rt)
        plugin.strict_mode = False  # explicit lax

        src_root = tmp_path / "src_lax"
        dst_root = tmp_path / "dst_lax"
        _seed_real_file(rt, "local", src_root / "ok.txt", content=b"compliant")

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        report = rt.migration.apply(plan)

        assert report.moved_count == 1
        assert report.moves[0].outcome == MigrationOutcome.MOVED
        assert report.moves[0].error is None

    def test_no_verify_migration_succeeds_in_lax(self, runtime_with_plugin, tmp_path):
        # In lax mode, even verify_hash=False is allowed (the plugin
        # observes but doesn't refuse).
        rt = runtime_with_plugin
        plugin = _get_atrium_safety_plugin(rt)
        plugin.strict_mode = False

        src_root = tmp_path / "src_noverify_lax"
        dst_root = tmp_path / "dst_noverify_lax"
        _seed_real_file(rt, "local", src_root / "skipped.txt")

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        report = rt.migration.apply(plan, verify_hash=False)

        assert report.moved_count == 1
        assert report.moves[0].outcome == MigrationOutcome.MOVED


class TestStrictModeAllowsCompliantWrites:
    """Strict mode refuses writes where verification was skipped, but
    compliant writes (verify_hash=True, the default) should pass through
    unchanged."""

    def test_strict_allows_verified_migration(self, runtime_with_plugin, tmp_path):
        rt = runtime_with_plugin
        plugin = _get_atrium_safety_plugin(rt)
        plugin.strict_mode = True

        src_root = tmp_path / "src_strict_ok"
        dst_root = tmp_path / "dst_strict_ok"
        _seed_real_file(rt, "local", src_root / "verified.txt")

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        # verify_hash=True is the default; the source-side verify will
        # pass src_xxhash to the hook, which the plugin will accept.
        report = rt.migration.apply(plan)

        assert report.moved_count == 1
        assert report.moves[0].outcome == MigrationOutcome.MOVED
        assert report.moves[0].error is None


class TestStrictModeRefusesNonCompliantWrites:
    """The headline integration test: strict mode + verify_hash=False
    triggers the plugin's refusal, propagating ComplianceError up
    through MigrationService, which turns the move into FAILED with
    the refusal reason in MigrationMove.error.
    """

    def test_strict_refuses_skipped_verify(self, runtime_with_plugin, tmp_path):
        rt = runtime_with_plugin
        plugin = _get_atrium_safety_plugin(rt)
        plugin.strict_mode = True

        src_root = tmp_path / "src_refused"
        dst_root = tmp_path / "dst_refused"
        _seed_real_file(rt, "local", src_root / "rejected.txt")

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        # verify_hash=False -- this is what the plugin should refuse.
        report = rt.migration.apply(plan, verify_hash=False)

        # The migration should NOT have moved the file
        assert report.moved_count == 0
        assert report.failed_count == 1

        move = report.moves[0]
        assert move.outcome == MigrationOutcome.FAILED
        assert move.error is not None

        # The error message should explain WHY the write was refused.
        # This is what users see -- it should help them understand the
        # plugin's role and what to do (either re-run with verification
        # enabled, or disable strict mode if they trust the source).
        assert "ComplianceError" in move.error
        assert "Constitution Principle 2" in move.error
        assert "Hash-Verify-Before-Move" in move.error

    def test_strict_refusal_does_not_corrupt_index(self, runtime_with_plugin, tmp_path):
        # When the plugin refuses, the dst was already written but
        # MigrationService caught the refusal in its outer
        # exception-boundary BEFORE updating the FileEntity index.
        # Therefore the FileEntity should still point at the SOURCE
        # path -- the index reflects ground truth (the source still
        # has the bytes; the dst is "leaked" but unindexed, picked up
        # by the next ``curator scan`` as a duplicate).
        rt = runtime_with_plugin
        plugin = _get_atrium_safety_plugin(rt)
        plugin.strict_mode = True

        src_root = tmp_path / "src_index"
        dst_root = tmp_path / "dst_index"
        seeded = _seed_real_file(rt, "local", src_root / "test.txt")

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        report = rt.migration.apply(plan, verify_hash=False)
        assert report.moves[0].outcome == MigrationOutcome.FAILED

        # FileEntity still points at the source
        entity = rt.file_repo.get(seeded.curator_id)
        assert entity is not None
        assert entity.source_id == "local"
        assert entity.source_path == str(src_root / "test.txt")

        # Source file still exists (NOT trashed)
        assert (src_root / "test.txt").exists()


class TestCoexistenceWithOtherHookimpls:
    """The plugin should not interfere with other plugins or test
    fixtures that register their own ``curator_source_write_post``
    hookimpls. All hookimpls fire on each event."""

    def test_plugin_coexists_with_test_recorder(self, runtime_with_plugin, tmp_path):
        rt = runtime_with_plugin
        plugin = _get_atrium_safety_plugin(rt)
        plugin.strict_mode = False  # don't refuse

        # Register an additional recorder to count hook firings
        from curator.plugins import hookimpl
        calls: list[dict] = []

        class _Recorder:
            @hookimpl
            def curator_source_write_post(
                self, source_id, file_id, src_xxhash, written_bytes_len,
            ):
                calls.append({
                    "source_id": source_id,
                    "src_xxhash": src_xxhash,
                    "written_bytes_len": written_bytes_len,
                })

        recorder = _Recorder()
        rt.pm.register(recorder)
        try:
            src_root = tmp_path / "src_coexist"
            dst_root = tmp_path / "dst_coexist"
            _seed_real_file(rt, "local", src_root / "co.txt")

            plan = rt.migration.plan(
                src_source_id="local", src_root=str(src_root),
                dst_source_id="local:vault", dst_root=str(dst_root),
            )
            report = rt.migration.apply(plan)
        finally:
            rt.pm.unregister(recorder)

        # Migration succeeded
        assert report.moved_count == 1
        # Recorder saw the event (so did atrium_safety, but its hookimpl
        # is silent in lax mode -- it doesn't show up in ``calls``).
        assert len(calls) == 1
