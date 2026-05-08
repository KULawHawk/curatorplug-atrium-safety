"""Decision logic for compliance enforcement.

Given a ``curator_source_write_post`` invocation, decide whether the
write is compliant with Atrium Constitution Principle 2 and, if not,
what to do about it.

Per DM-1 (ratified): soft enforcement -- violations raise
``ComplianceError``. The plugin module wraps the decision in a
hookimpl that performs the raise.

This module is pure (no I/O, no Curator imports) so it's trivially
unit-testable. The plugin module is the integration boundary that
ties the decision to the actual Pluggy hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from curatorplug.atrium_safety.exceptions import ComplianceError


class EnforcementVerdict(str, Enum):
    """The four possible verdicts on a ``curator_source_write_post`` event.

    * ``OK`` -- the write is compliant; nothing to do.
    * ``SKIPPED`` -- enforcement is disabled or the write is in a
      category we don't enforce (e.g., zero-byte writes; future:
      whitelisted plugins).
    * ``WARN`` -- the write is suspicious but not refused (e.g., advisory
      mode would flag this). v0.1.0 doesn't actually use this verdict
      since DM-1 ratified soft enforcement, not advisory; reserved for
      future use.
    * ``REFUSE`` -- the write violates Constitution Principle 2 and the
      plugin must raise ``ComplianceError`` to refuse it.
    """

    OK = "ok"
    SKIPPED = "skipped"
    WARN = "warn"
    REFUSE = "refuse"


@dataclass(frozen=True)
class EnforcementDecision:
    """A decision plus a human-readable explanation.

    The plugin module renders ``message`` as the ``ComplianceError``
    message when ``verdict == REFUSE``. ``OK`` and ``SKIPPED`` decisions
    have a message too, which can be surfaced for debug logging.
    """

    verdict: EnforcementVerdict
    message: str


def decide(
    *,
    source_id: str,
    file_id: str,
    src_xxhash: str | None,
    written_bytes_len: int,
    strict_mode: bool = False,
) -> EnforcementDecision:
    """Decide what to do about a ``curator_source_write_post`` invocation.

    Args:
        source_id: as passed to the hook -- the destination source.
        file_id: as passed to the hook -- the dst identifier.
        src_xxhash: as passed to the hook -- ``None`` if the caller
            (e.g., MigrationService) skipped its own verify, otherwise
            the source's xxh3_128 hex digest.
        written_bytes_len: as passed to the hook.
        strict_mode: when True, ``src_xxhash is None`` triggers a
            REFUSE verdict. When False (default), ``None`` triggers
            only an OK verdict with an informational message.

    Returns:
        An :class:`EnforcementDecision` whose ``verdict`` field tells
        the plugin module what to do (OK = no-op; REFUSE = raise).

    Decision policy (v0.1.0):

    * ``strict_mode=True`` AND ``src_xxhash is None`` \u2192 REFUSE. The
      caller bypassed Constitution Principle 2's verification step;
      this plugin refuses to let the write proceed silently.
    * Any other case \u2192 OK. The hookspec contract guarantees the hook
      only fires after a successful write; if the caller verified
      (``src_xxhash`` is set), Constitution Principle 2 was honored.

    Future versions may add:

    * Independent re-read verification (requires Curator-side hookspec
      amendment to give plugins access to the plugin manager).
    * Per-source-id whitelists / blacklists.
    * Configurable advisory mode (would yield WARN verdicts).
    """
    # Defensive: source_id and file_id are not currently used in decisions
    # but accepted as kwargs so future policies can use them without
    # changing the signature. Reference them so static analyzers don't
    # warn about unused parameters.
    _ = source_id, file_id

    # Defensive: a zero-byte write is technically allowed by the hookspec
    # but worth flagging.
    if written_bytes_len < 0:
        return EnforcementDecision(
            verdict=EnforcementVerdict.REFUSE,
            message=(
                f"compliance: written_bytes_len={written_bytes_len} is "
                "negative; this is a hookspec contract violation."
            ),
        )

    # Strict mode: refuse if the source side skipped verification.
    if strict_mode and src_xxhash is None:
        return EnforcementDecision(
            verdict=EnforcementVerdict.REFUSE,
            message=(
                "compliance: write proceeded without source-side hash "
                "verification (src_xxhash is None) while strict_mode=True. "
                "Atrium Constitution Principle 2 (Hash-Verify-Before-Move) "
                "requires verification; refusing the write."
            ),
        )

    # Default: the caller verified (src_xxhash is set) OR we're not in
    # strict mode. Either way, the write is compliant per our policy.
    if src_xxhash is None:
        return EnforcementDecision(
            verdict=EnforcementVerdict.OK,
            message=(
                "compliance: write proceeded without source-side hash "
                "verification (src_xxhash is None); strict_mode=False, "
                "so allowed. Caller is trusted to have its own discipline."
            ),
        )
    return EnforcementDecision(
        verdict=EnforcementVerdict.OK,
        message=(
            f"compliance: write verified at source (xxh3={src_xxhash[:12]}\u2026); "
            "Constitution Principle 2 honored."
        ),
    )


def enforce(decision: EnforcementDecision) -> None:
    """Raise :class:`ComplianceError` iff ``decision.verdict == REFUSE``.

    Pure function (no side effects). Separated from :func:`decide` so
    tests can verify the decision logic in isolation, then verify the
    enforcement step separately.
    """
    if decision.verdict == EnforcementVerdict.REFUSE:
        raise ComplianceError(decision.message)
