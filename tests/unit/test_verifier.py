"""Unit tests for the verifier module's pure hash utilities."""

from __future__ import annotations

import xxhash

from curatorplug.atrium_safety.verifier import compute_xxh3, verify_xxh3


class TestComputeXxh3:
    def test_returns_hex_string(self):
        result = compute_xxh3(b"hello world")
        assert isinstance(result, str)
        # xxh3_128 produces a 32-character hex digest
        assert len(result) == 32
        # All chars are valid hex
        int(result, 16)

    def test_empty_bytes_has_known_digest(self):
        # Stable across xxhash versions -- this is the canonical xxh3_128
        # of empty bytes.
        assert compute_xxh3(b"") == "99aa06d3014798d86001c324468d497f"

    def test_matches_xxhash_library_directly(self):
        data = b"the quick brown fox jumps over the lazy dog"
        expected = xxhash.xxh3_128(data).hexdigest()
        assert compute_xxh3(data) == expected

    def test_different_inputs_yield_different_hashes(self):
        assert compute_xxh3(b"a") != compute_xxh3(b"b")
        assert compute_xxh3(b"abc") != compute_xxh3(b"abd")


class TestVerifyXxh3:
    def test_match_returns_true(self):
        data = b"some bytes\n" * 100
        expected = compute_xxh3(data)
        assert verify_xxh3(data, expected) is True

    def test_mismatch_returns_false(self):
        data = b"some bytes\n" * 100
        wrong_hash = "deadbeef" * 4
        assert verify_xxh3(data, wrong_hash) is False

    def test_case_insensitive_match(self):
        data = b"case test"
        expected = compute_xxh3(data)
        # Upper-case version still matches
        assert verify_xxh3(data, expected.upper()) is True
        # Mixed case
        upper_lower = "".join(
            ch.upper() if i % 2 else ch for i, ch in enumerate(expected)
        )
        assert verify_xxh3(data, upper_lower) is True

    def test_empty_expected_returns_false(self):
        # Plugin authors who pass empty / None get strict False, never
        # an exception.
        assert verify_xxh3(b"anything", "") is False
        assert verify_xxh3(b"anything", None) is False  # type: ignore[arg-type]
