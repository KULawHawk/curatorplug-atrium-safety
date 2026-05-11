"""T-B02 unit tests for curator_pre_trash hookimpl (v0.4.0+)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from curatorplug.atrium_safety.plugin import AtriumSafetyPlugin


def _make_file(status: str = "active", expires_at=None, source_path: str = "/tmp/x.txt"):
    """Build a duck-typed FileEntity-like object for hookimpl input."""
    return SimpleNamespace(
        curator_id="00000000-0000-0000-0000-000000000001",
        source_id="local",
        source_path=source_path,
        status=status,
        expires_at=expires_at,
    )


def test_active_file_is_not_vetoed():
    """status='active' (default) -> hookimpl returns None (allow trash)."""
    p = AtriumSafetyPlugin(strict_mode=False)
    result = p.curator_pre_trash(file=_make_file(status="active"), reason="user trashed")
    assert result is None


def test_provisional_file_is_not_vetoed():
    """Only 'vital' triggers veto."""
    p = AtriumSafetyPlugin(strict_mode=False)
    result = p.curator_pre_trash(file=_make_file(status="provisional"), reason="r")
    assert result is None


def test_junk_file_is_not_vetoed():
    """Junk files are explicitly trashable."""
    p = AtriumSafetyPlugin(strict_mode=False)
    result = p.curator_pre_trash(file=_make_file(status="junk"), reason="r")
    assert result is None


def test_vital_file_without_expires_at_is_vetoed():
    """Vital + no expiry -> veto."""
    p = AtriumSafetyPlugin(strict_mode=False)
    f = _make_file(status="vital", expires_at=None)
    result = p.curator_pre_trash(file=f, reason="r")
    assert result is not None
    assert result.allow is False
    assert "vital" in result.reason.lower()
    assert result.plugin == "curatorplug.atrium_safety"


def test_vital_file_with_future_expires_at_is_vetoed():
    """Vital + expires_at in future -> veto (retention window active)."""
    p = AtriumSafetyPlugin(strict_mode=False)
    future = datetime.utcnow() + timedelta(days=365)
    f = _make_file(status="vital", expires_at=future)
    result = p.curator_pre_trash(file=f, reason="r")
    assert result is not None
    assert result.allow is False
    assert future.isoformat() in result.reason  # horizon mentioned


def test_vital_file_with_past_expires_at_is_allowed():
    """Vital + expires_at in past -> allow (retention period elapsed)."""
    p = AtriumSafetyPlugin(strict_mode=False)
    past = datetime.utcnow() - timedelta(days=1)
    f = _make_file(status="vital", expires_at=past)
    result = p.curator_pre_trash(file=f, reason="r")
    assert result is None  # allow


def test_pre_migration_003_file_treated_as_active():
    """File lacking .status attribute (pre-migration-003) -> assumed active, no veto."""
    p = AtriumSafetyPlugin(strict_mode=False)
    f = SimpleNamespace(
        curator_id="00000000-0000-0000-0000-000000000002",
        source_id="local",
        source_path="/tmp/old.txt",
        # No status attribute!
    )
    result = p.curator_pre_trash(file=f, reason="r")
    assert result is None


def test_status_none_treated_as_active():
    """status=None (defensive) -> treated as active, no veto."""
    p = AtriumSafetyPlugin(strict_mode=False)
    f = _make_file()
    f.status = None
    result = p.curator_pre_trash(file=f, reason="r")
    assert result is None


def test_audit_emission_silently_noops_without_pm():
    """When self.pm is None, audit emission must silently no-op (not crash)."""
    p = AtriumSafetyPlugin(strict_mode=False)
    assert p.pm is None
    # Should not raise
    result = p.curator_pre_trash(
        file=_make_file(status="vital"),
        reason="r",
    )
    assert result is not None  # Still vetoed


def test_audit_emission_fires_with_pm_when_hook_present():
    """When pm has curator_audit_event hook, veto path fires the audit."""
    p = AtriumSafetyPlugin(strict_mode=False)
    # Mock a pm with curator_audit_event hook
    mock_pm = MagicMock()
    mock_pm.hook.curator_audit_event = MagicMock()
    p.pm = mock_pm

    result = p.curator_pre_trash(
        file=_make_file(status="vital"),
        reason="r",
    )
    assert result.allow is False

    # Audit event was called
    mock_pm.hook.curator_audit_event.assert_called_once()
    call_kwargs = mock_pm.hook.curator_audit_event.call_args.kwargs
    assert call_kwargs["action"] == "compliance.retention_veto"
    assert call_kwargs["actor"] == "curatorplug.atrium_safety"
    assert call_kwargs["entity_type"] == "file"
    assert call_kwargs["details"]["phase"] == "pre_trash"


def test_audit_emits_retention_allow_when_horizon_elapsed():
    """When vital file's retention horizon has passed, audit emits 'retention_allow'."""
    p = AtriumSafetyPlugin(strict_mode=False)
    mock_pm = MagicMock()
    mock_pm.hook.curator_audit_event = MagicMock()
    p.pm = mock_pm

    past = datetime.utcnow() - timedelta(days=1)
    result = p.curator_pre_trash(
        file=_make_file(status="vital", expires_at=past),
        reason="r",
    )
    assert result is None  # allowed

    mock_pm.hook.curator_audit_event.assert_called_once()
    call_kwargs = mock_pm.hook.curator_audit_event.call_args.kwargs
    assert call_kwargs["action"] == "compliance.retention_allow"
