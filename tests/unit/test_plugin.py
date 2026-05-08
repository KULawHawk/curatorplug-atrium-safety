"""Unit tests for the plugin module.

Exercises the ``AtriumSafetyPlugin`` class's hookimpl method directly
(without going through Pluggy) and verifies the env-var-based strict
mode auto-detection. Pluggy integration via a real Curator runtime is
covered in the P3 integration tests (separate session).
"""

from __future__ import annotations

import pytest

from curatorplug.atrium_safety.exceptions import ComplianceError
from curatorplug.atrium_safety.plugin import (
    AtriumSafetyPlugin,
    _read_strict_mode_env,
)


class TestStrictModeEnvDetection:
    def test_unset_env_returns_false(self, monkeypatch):
        monkeypatch.delenv("CURATORPLUG_ATRIUM_SAFETY_STRICT", raising=False)
        assert _read_strict_mode_env() is False

    def test_empty_env_returns_false(self, monkeypatch):
        monkeypatch.setenv("CURATORPLUG_ATRIUM_SAFETY_STRICT", "")
        assert _read_strict_mode_env() is False

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "True", "yes", "YES", "on", "ON"])
    def test_truthy_strings_enable_strict(self, monkeypatch, val):
        monkeypatch.setenv("CURATORPLUG_ATRIUM_SAFETY_STRICT", val)
        assert _read_strict_mode_env() is True

    @pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off", "anything", "  "])
    def test_falsy_or_other_returns_false(self, monkeypatch, val):
        monkeypatch.setenv("CURATORPLUG_ATRIUM_SAFETY_STRICT", val)
        assert _read_strict_mode_env() is False


class TestAtriumSafetyPluginConstruction:
    def test_explicit_strict_mode_overrides_env(self, monkeypatch):
        monkeypatch.setenv("CURATORPLUG_ATRIUM_SAFETY_STRICT", "1")
        # Explicit kwarg wins over env
        p = AtriumSafetyPlugin(strict_mode=False)
        assert p.strict_mode is False

    def test_default_reads_from_env_when_strict(self, monkeypatch):
        monkeypatch.setenv("CURATORPLUG_ATRIUM_SAFETY_STRICT", "true")
        p = AtriumSafetyPlugin()
        assert p.strict_mode is True

    def test_default_reads_from_env_when_unset(self, monkeypatch):
        monkeypatch.delenv("CURATORPLUG_ATRIUM_SAFETY_STRICT", raising=False)
        p = AtriumSafetyPlugin()
        assert p.strict_mode is False


class TestHookimplBehavior:
    """Direct invocation of the hookimpl method (bypassing Pluggy).

    This is a pure unit test -- the Pluggy plumbing (entry-point
    discovery, hook firing through plugin manager) is exercised in the
    P3 integration tests. Here we just verify that calling the method
    with various args produces the right behavior (raise vs. no-raise).
    """

    def test_compliant_write_does_not_raise_in_lax(self, plugin_lax):
        # Should not raise -- compliant write, lax mode
        plugin_lax.curator_source_write_post(
            source_id="local",
            file_id="/path",
            src_xxhash="a" * 32,
            written_bytes_len=100,
        )

    def test_compliant_write_does_not_raise_in_strict(self, plugin_strict):
        # Should not raise -- compliant write even in strict mode
        plugin_strict.curator_source_write_post(
            source_id="local",
            file_id="/path",
            src_xxhash="a" * 32,
            written_bytes_len=100,
        )

    def test_skipped_verify_does_not_raise_in_lax(self, plugin_lax):
        # Lax mode: skipped verify is OK (caller is trusted)
        plugin_lax.curator_source_write_post(
            source_id="local",
            file_id="/path",
            src_xxhash=None,
            written_bytes_len=100,
        )

    def test_skipped_verify_raises_in_strict(self, plugin_strict):
        with pytest.raises(ComplianceError) as exc_info:
            plugin_strict.curator_source_write_post(
                source_id="local",
                file_id="/path",
                src_xxhash=None,
                written_bytes_len=100,
            )
        # The message must explain WHY it was refused -- this is what
        # users see in MigrationMove.error after a refused migration.
        assert "Constitution Principle 2" in str(exc_info.value)
        assert "Hash-Verify-Before-Move" in str(exc_info.value)

    def test_refusal_message_includes_strict_mode_context(self, plugin_strict):
        # The exception message should help users understand WHY the
        # plugin refused. Strict mode being mentioned is part of that.
        with pytest.raises(ComplianceError, match="strict_mode=True"):
            plugin_strict.curator_source_write_post(
                source_id="local",
                file_id="/path",
                src_xxhash=None,
                written_bytes_len=100,
            )

    def test_negative_bytes_len_raises_regardless_of_mode(self, plugin_lax):
        # Hookspec contract violation -- always refuse.
        with pytest.raises(ComplianceError, match="hookspec contract violation"):
            plugin_lax.curator_source_write_post(
                source_id="local",
                file_id="/path",
                src_xxhash="a" * 32,
                written_bytes_len=-5,
            )
