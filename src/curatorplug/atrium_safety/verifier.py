"""xxhash3_128 utilities for the atrium-safety plugin.

Per DM-2 (ratified): the plugin uses a single algorithm (xxh3_128),
matching Curator's canonical hash. Dual-algorithm verification
(xxh3 + sha256) was rejected because the threat model is
*misbehaving plugins*, not *adversarial attackers*. A plugin author
who wants to hide a bad write doesn't need to engineer a hash
collision; they just need to skip verification. So the value of a
second cryptographically-stronger algorithm is essentially zero
for our threat model.

This module is small on purpose. The functions are pure (no I/O, no
side effects) so they're trivially unit-testable and reusable.
"""

from __future__ import annotations

import xxhash


def compute_xxh3(data: bytes) -> str:
    """Compute the xxhash3_128 hex digest of a byte string.

    Args:
        data: The bytes to hash. Whole-buffer in-memory; this matches
            the in-memory contract of ``curator_source_write`` (per the
            v0.40 hookspec). Streaming computation is unnecessary at
            this layer.

    Returns:
        The 32-character hex digest of the xxhash3_128 of ``data``.

    Examples:
        >>> compute_xxh3(b"")
        '99aa06d3014798d86001c324468d497f'
        >>> compute_xxh3(b"hello")  # doctest: +ELLIPSIS
        '...'
    """
    return xxhash.xxh3_128(data).hexdigest()


def verify_xxh3(data: bytes, expected: str) -> bool:
    """Return True iff ``data``'s xxh3_128 equals ``expected``.

    Args:
        data: The bytes to verify.
        expected: The expected xxh3_128 hex digest, case-insensitive.
            Plugin authors who pass ``None`` or empty strings get
            ``False`` -- the comparison is strict; missing expected
            value is treated as "cannot verify".

    Returns:
        True if ``data``'s computed xxh3_128 matches ``expected``
        (case-insensitive); False otherwise. False is also returned
        when ``expected`` is empty, None, or otherwise falsy.
    """
    if not expected:
        return False
    actual = compute_xxh3(data)
    return actual.lower() == expected.lower()
