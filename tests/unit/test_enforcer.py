"""Unit tests for the enforcer module's decision logic."""

from __future__ import annotations

import pytest

from curatorplug.atrium_safety.enforcer import (
    EnforcementDecision,
    EnforcementVerdict,
    decide,
    enforce,
)
from curatorplug.atrium_safety.exceptions import ComplianceError


class TestDecide:
    def test_compliant_write_with_hash_returns_ok(self):
        d = decide(
            source_id="local",
            file_id="/some/path",
            src_xxhash="abc123def456" * 2 + "0000" * 2,  # 32-char placeholder
            written_bytes_len=1024,
            strict_mode=False,
        )
        assert d.verdict == EnforcementVerdict.OK
        assert "verified at source" in d.message

    def test_compliant_write_with_hash_in_strict_mode_still_ok(self):
        d = decide(
            source_id="local",
            file_id="/some/path",
            src_xxhash="a" * 32,
            written_bytes_len=1024,
            strict_mode=True,
        )
        assert d.verdict == EnforcementVerdict.OK

    def test_skipped_verify_in_lax_mode_returns_ok(self):
        d = decide(
            source_id="local",
            file_id="/some/path",
            src_xxhash=None,
            written_bytes_len=1024,
            strict_mode=False,
        )
        assert d.verdict == EnforcementVerdict.OK
        assert "strict_mode=False" in d.message

    def test_skipped_verify_in_strict_mode_returns_refuse(self):
        d = decide(
            source_id="local",
            file_id="/some/path",
            src_xxhash=None,
            written_bytes_len=1024,
            strict_mode=True,
        )
        assert d.verdict == EnforcementVerdict.REFUSE
        # Message must mention Constitution Principle 2 by name -- the
        # plugin's whole job is to defend it, so users seeing the
        # exception should know what's being enforced.
        assert "Constitution Principle 2" in d.message
        assert "Hash-Verify-Before-Move" in d.message

    def test_negative_bytes_len_always_refuses(self):
        d = decide(
            source_id="local",
            file_id="/x",
            src_xxhash="a" * 32,
            written_bytes_len=-1,
            strict_mode=False,
        )
        assert d.verdict == EnforcementVerdict.REFUSE
        assert "hookspec contract violation" in d.message

    def test_zero_bytes_is_compliant(self):
        # Zero-byte writes (empty files) are legal.
        d = decide(
            source_id="local",
            file_id="/empty",
            src_xxhash="99aa06d3014798d86001c324468d497f",  # xxh3 of b""
            written_bytes_len=0,
            strict_mode=True,
        )
        assert d.verdict == EnforcementVerdict.OK


class TestEnforce:
    def test_ok_decision_does_not_raise(self):
        d = EnforcementDecision(
            verdict=EnforcementVerdict.OK,
            message="all good",
        )
        # Must not raise
        enforce(d)

    def test_skipped_decision_does_not_raise(self):
        d = EnforcementDecision(
            verdict=EnforcementVerdict.SKIPPED,
            message="enforcement disabled",
        )
        enforce(d)

    def test_warn_decision_does_not_raise(self):
        # WARN is reserved for future advisory mode; in v0.1.0 it's
        # effectively the same as OK -- doesn't raise.
        d = EnforcementDecision(
            verdict=EnforcementVerdict.WARN,
            message="suspicious but allowed",
        )
        enforce(d)

    def test_refuse_decision_raises_compliance_error(self):
        d = EnforcementDecision(
            verdict=EnforcementVerdict.REFUSE,
            message="non-compliant write",
        )
        with pytest.raises(ComplianceError, match="non-compliant write"):
            enforce(d)

    def test_compliance_error_carries_message_attr(self):
        d = EnforcementDecision(
            verdict=EnforcementVerdict.REFUSE,
            message="explicit refusal reason",
        )
        try:
            enforce(d)
        except ComplianceError as e:
            assert e.message == "explicit refusal reason"
            # str(e) also gives the message
            assert str(e) == "explicit refusal reason"
        else:
            pytest.fail("ComplianceError should have been raised")
