"""Exception types raised by the atrium-safety plugin.

DM-1 ratified soft enforcement: violations raise ``ComplianceError``,
which propagates through the caller's exception-boundary and turns the
operation into the appropriate failure outcome (e.g.,
``MigrationOutcome.FAILED`` with the exception's message in
``MigrationMove.error`` for migrations).

Recovery is automatic: the next call to the same plugin gets a fresh
chance; uninstalling this plugin disables enforcement entirely. No
operator intervention required to "unblock" a non-compliant plugin --
that would violate Atrium Principle 1 (Reversibility) at the
operational level.
"""

from __future__ import annotations


class ComplianceError(Exception):
    """Raised when a write violates Atrium Constitution Principle 2.

    Carries a freeform message describing what was non-compliant. The
    plugin's caller (typically ``MigrationService._cross_source_transfer``
    inside Curator core) catches this in its outer exception-boundary
    and turns it into a per-file failure outcome.

    Examples of conditions that raise this:

    * Strict mode enabled AND ``src_xxhash is None`` (verification was
      skipped at the source side, e.g. by passing ``--no-verify-hash``).
      Constitutional Principle 2 mandates verification; opting out
      while this plugin is in strict mode is non-compliant.
    * (Future) Independent re-read verification of dst yielded a
      different hash than the recorded src_xxhash. Currently deferred
      pending a Curator-side mechanism for plugins to access the
      plugin manager from inside a hookimpl (see DESIGN.md \u00a75).
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
