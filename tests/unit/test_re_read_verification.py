"""Unit tests for the re-read verification logic introduced in v0.2.0.

Exercises ``AtriumSafetyPlugin._verify_via_re_read`` and the chunked
read loop in ``_read_all_via_hook`` using a hand-rolled fake plugin
manager. No real Curator runtime needed; integration with the actual
runtime is covered in ``tests/integration/test_curator_runtime.py``.
"""

from __future__ import annotations

import pytest

from curatorplug.atrium_safety.enforcer import EnforcementVerdict
from curatorplug.atrium_safety.exceptions import ComplianceError
from curatorplug.atrium_safety.plugin import (
    AtriumSafetyPlugin,
    _RE_READ_CHUNK_SIZE,
)
from curatorplug.atrium_safety.verifier import compute_xxh3


class _FakeHook:
    """Mimics pluggy's hook caller for ``curator_source_read_bytes``.

    Stores a callable that's called with each (source_id, file_id,
    offset, length) tuple and returns either bytes (the chunk) or None
    (no plugin owns this source_id). The fake always wraps the result
    in a list to match pluggy's actual return shape.
    """

    def __init__(self, read_fn):
        self._read_fn = read_fn

    def __call__(self, *, source_id, file_id, offset, length):
        result = self._read_fn(source_id, file_id, offset, length)
        return [result]  # pluggy returns a list of plugin results


class _FakePm:
    """Mimics pluggy.PluginManager for re-read tests.

    Has a ``.hook.curator_source_read_bytes(...)`` attribute that
    delegates to a user-provided ``read_fn``. That's the only pm
    surface the plugin uses for re-read verification.
    """

    def __init__(self, read_fn):
        class _HookNamespace:
            curator_source_read_bytes = _FakeHook(read_fn)
        self.hook = _HookNamespace()


def _make_chunk_returning_pm(content: bytes):
    """Build a fake pm whose curator_source_read_bytes returns ``content``
    in chunks (matching the real pluggy contract: a short read indicates
    EOF). Useful for the happy-path tests."""

    def read_fn(source_id, file_id, offset, length):
        if offset >= len(content):
            return b""  # EOF
        return content[offset:offset + length]

    return _FakePm(read_fn)


def _make_corrupt_pm(actual_content: bytes):
    """Build a fake pm that returns ``actual_content`` regardless of
    what was supposedly written. Used to simulate a misbehaving source
    plugin or transient corruption -- the safety plugin's re-read sees
    different bytes than the migration's source-hash implies."""
    return _make_chunk_returning_pm(actual_content)


def _make_unowned_source_pm():
    """Build a fake pm that returns None for every read (no plugin
    owns the source_id). Used to test the conservative fallback."""

    def read_fn(source_id, file_id, offset, length):
        return None

    return _FakePm(read_fn)


def _make_raising_pm(exc: Exception):
    """Build a fake pm whose curator_source_read_bytes raises ``exc``.
    Used to test that re-read failures don't crash the migration --
    the plugin should conservatively log and skip verify."""

    def read_fn(source_id, file_id, offset, length):
        raise exc

    return _FakePm(read_fn)


# ---------------------------------------------------------------------------
# Tests for _verify_via_re_read decision logic
# ---------------------------------------------------------------------------


class TestReReadHappyPath:
    """When the dst's actual bytes match the expected hash, the
    verdict is OK and no exception is raised."""

    def test_match_returns_ok_in_lax(self):
        plugin = AtriumSafetyPlugin(strict_mode=False)
        content = b"hello world\n" * 100
        plugin.pm = _make_chunk_returning_pm(content)
        decision = plugin._verify_via_re_read(
            source_id="local",
            file_id="/some/file",
            expected_xxhash=compute_xxh3(content),
        )
        assert decision.verdict == EnforcementVerdict.OK
        assert "matches" in decision.message

    def test_match_returns_ok_in_strict(self):
        plugin = AtriumSafetyPlugin(strict_mode=True)
        content = b"strict mode test\n"
        plugin.pm = _make_chunk_returning_pm(content)
        decision = plugin._verify_via_re_read(
            source_id="local:vault",
            file_id="/some/file",
            expected_xxhash=compute_xxh3(content),
        )
        assert decision.verdict == EnforcementVerdict.OK


class TestReReadMismatch:
    """When the dst bytes don't match the expected hash, behavior
    depends on strict_mode: refuse in strict, warn in lax."""

    def test_mismatch_refuses_in_strict(self):
        plugin = AtriumSafetyPlugin(strict_mode=True)
        # The dst plugin returns "corrupt" but the expected hash is for "real"
        actual = b"corrupt-bytes"
        plugin.pm = _make_corrupt_pm(actual)
        expected = compute_xxh3(b"real-bytes-different")
        decision = plugin._verify_via_re_read(
            source_id="local",
            file_id="/x",
            expected_xxhash=expected,
        )
        assert decision.verdict == EnforcementVerdict.REFUSE
        # Message must include both hashes so users can debug
        assert expected in decision.message
        assert compute_xxh3(actual) in decision.message
        # Message must explain WHY (the constitutional reasoning)
        assert "Constitution Principle 2" in decision.message
        assert "Hash-Verify-Before-Move" in decision.message

    def test_mismatch_warns_in_lax(self):
        plugin = AtriumSafetyPlugin(strict_mode=False)
        actual = b"actually different"
        plugin.pm = _make_corrupt_pm(actual)
        decision = plugin._verify_via_re_read(
            source_id="local",
            file_id="/x",
            expected_xxhash=compute_xxh3(b"expected different"),
        )
        assert decision.verdict == EnforcementVerdict.WARN
        # Message should still surface the diagnostic info
        assert "lax mode" in decision.message
        assert "not refusing" in decision.message


class TestReReadConservativeFallbacks:
    """When re-read can't be performed for non-compliance reasons (no
    plugin owns the source_id, exception during read), the plugin
    conservatively returns OK -- it doesn't refuse a write just
    because re-read itself failed."""

    def test_no_plugin_owns_source_returns_ok(self):
        plugin = AtriumSafetyPlugin(strict_mode=True)
        plugin.pm = _make_unowned_source_pm()
        decision = plugin._verify_via_re_read(
            source_id="unknown",
            file_id="/x",
            expected_xxhash="a" * 32,
        )
        assert decision.verdict == EnforcementVerdict.OK
        assert "no plugin handled" in decision.message

    def test_read_exception_returns_ok(self):
        plugin = AtriumSafetyPlugin(strict_mode=True)
        plugin.pm = _make_raising_pm(IOError("network timeout"))
        decision = plugin._verify_via_re_read(
            source_id="local",
            file_id="/x",
            expected_xxhash="a" * 32,
        )
        assert decision.verdict == EnforcementVerdict.OK
        assert "OSError" in decision.message or "IOError" in decision.message
        assert "skipping independent verify" in decision.message


class TestReReadChunkLoop:
    """The chunk-reading loop in _read_all_via_hook must correctly
    assemble multi-chunk reads and detect EOF."""

    def test_short_read_signals_eof(self):
        # Content shorter than one chunk -- single read returns < chunk
        # size, loop terminates.
        plugin = AtriumSafetyPlugin(strict_mode=False)
        content = b"tiny"
        plugin.pm = _make_chunk_returning_pm(content)
        result = plugin._read_all_via_hook("local", "/x")
        assert result == content

    def test_multi_chunk_read_assembles_correctly(self):
        plugin = AtriumSafetyPlugin(strict_mode=False)
        # Content larger than one chunk -- the read loop iterates
        # multiple times, assembling chunks.
        content = b"a" * (_RE_READ_CHUNK_SIZE * 2 + 100)
        plugin.pm = _make_chunk_returning_pm(content)
        result = plugin._read_all_via_hook("local", "/x")
        assert result == content
        assert len(result) == _RE_READ_CHUNK_SIZE * 2 + 100

    def test_empty_content(self):
        plugin = AtriumSafetyPlugin(strict_mode=False)
        plugin.pm = _make_chunk_returning_pm(b"")
        result = plugin._read_all_via_hook("local", "/x")
        # Empty content but plugin DOES own the source -- assembled
        # bytes are empty; not None.
        assert result == b""


class TestReReadIntegrationWithCuratorSourceWritePost:
    """Verify that the post-write hook actually invokes re-read when
    pm is set, and propagates ComplianceError on strict-mode mismatch."""

    def test_post_write_does_not_re_read_when_pm_is_none(self):
        # No pm set (plugin running against Curator < 1.1.2). Re-read
        # phase is skipped entirely; only decide() phase runs.
        plugin = AtriumSafetyPlugin(strict_mode=True)
        assert plugin.pm is None  # constructor default
        # Compliant write per decide() (src_xxhash provided, strict but
        # not None). Should not raise even though no re-read happens.
        plugin.curator_source_write_post(
            source_id="local",
            file_id="/x",
            src_xxhash="a" * 32,
            written_bytes_len=100,
        )

    def test_post_write_re_reads_when_pm_is_set_strict(self):
        # pm is set, strict mode, dst bytes match expected -> no raise
        plugin = AtriumSafetyPlugin(strict_mode=True)
        content = b"verified bytes"
        plugin.pm = _make_chunk_returning_pm(content)
        # Should NOT raise (re-read confirms compliance)
        plugin.curator_source_write_post(
            source_id="local",
            file_id="/x",
            src_xxhash=compute_xxh3(content),
            written_bytes_len=len(content),
        )

    def test_post_write_re_read_mismatch_raises_in_strict(self):
        plugin = AtriumSafetyPlugin(strict_mode=True)
        actual = b"sneaky corrupt bytes"
        plugin.pm = _make_corrupt_pm(actual)
        # Expected hash doesn't match what fake pm will return -> REFUSE
        with pytest.raises(ComplianceError) as exc_info:
            plugin.curator_source_write_post(
                source_id="local",
                file_id="/x",
                src_xxhash=compute_xxh3(b"real bytes"),
                written_bytes_len=len(b"real bytes"),
            )
        assert "independent re-read verification FAILED" in str(exc_info.value)

    def test_post_write_re_read_mismatch_does_not_raise_in_lax(self):
        plugin = AtriumSafetyPlugin(strict_mode=False)
        actual = b"different bytes"
        plugin.pm = _make_corrupt_pm(actual)
        # Should NOT raise in lax mode
        plugin.curator_source_write_post(
            source_id="local",
            file_id="/x",
            src_xxhash=compute_xxh3(b"original bytes"),
            written_bytes_len=len(b"original bytes"),
        )

    def test_post_write_does_not_re_read_when_src_xxhash_is_none(self):
        # Even with pm set, if src_xxhash is None, re-read can't be
        # done (no expected value to compare against). The decide()
        # phase still runs (and refuses in strict, allows in lax).
        plugin_strict = AtriumSafetyPlugin(strict_mode=True)
        plugin_strict.pm = _make_chunk_returning_pm(b"any bytes")
        # decide() refuses because src_xxhash=None + strict_mode=True
        with pytest.raises(ComplianceError) as exc_info:
            plugin_strict.curator_source_write_post(
                source_id="local",
                file_id="/x",
                src_xxhash=None,
                written_bytes_len=100,
            )
        # The error is from the decide() phase, not re-read
        assert "Constitution Principle 2" in str(exc_info.value)
        # Should NOT mention re-read (we never got there)
        assert "independent re-read" not in str(exc_info.value)
