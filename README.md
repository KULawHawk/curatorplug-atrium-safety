# curatorplug-atrium-safety

**Constitutional compliance plugin for [Curator](https://github.com/KULawHawk/Curator).**
Enforces Atrium Constitution **Principle 2 (Hash-Verify-Before-Move)** as a
*cross-cutting layer* over Curator's plugin ecosystem.

> Status: v0.3.0 — structured audit emission active. Auto-discovered via setuptools entry point.
> Default mode is lax (refuses nothing); strict mode is opt-in via env var. Requires Curator >= 1.1.2 for re-read; Curator >= 1.1.3 for audit emission. Gracefully degrades on older Curator.

## What this plugin does

When Curator's `MigrationService` performs a cross-source migration, it
calls each source plugin's `curator_source_write` hook to write bytes
to the destination. This plugin listens for the `curator_source_write_post`
notification (added in [Curator v1.1.1](https://github.com/KULawHawk/Curator/releases/tag/v1.1.1))
that fires after each successful write and:

- **In default (lax) mode** — observes the event and logs a debug
  message. No-op on the migration's outcome. Useful for verifying the
  plugin is actually loading and the hook is firing.
- **In strict mode** (env var `CURATORPLUG_ATRIUM_SAFETY_STRICT=1`) —
  *refuses* writes where the source side skipped its own
  hash-verification (e.g., `--no-verify-hash` was passed to the
  migration). Refusal is by raising `ComplianceError`, which propagates
  through Curator's exception boundary and turns the per-file move into
  `MigrationOutcome.FAILED` with the refusal reason in
  `MigrationMove.error`. The migration as a whole continues; each
  refused file is FAILED individually.

This is **soft enforcement** per [DM-1 of `DESIGN.md`](DESIGN.md#dm-1--enforcement-mode):
recovery is automatic (the next call gets a fresh chance; uninstalling
this plugin disables enforcement entirely; no operator intervention
needed to "unblock" a non-compliant plugin).

## Why this plugin exists

There is currently no enforcement that a third-party Curator source
plugin's `curator_source_write` actually performs hash-verification on
its own. A bad write hook could return success without verifying — and
while Migration's own re-read verify catches transport corruption, it
relies on the `curator_source_read_bytes` hook being honest too. This
plugin adds a *retrospective compliance layer* that flags writes
proceeding without verification, independent of whether the underlying
plugins are well-behaved.

The longer-term value (deferred to a future Curator-side enhancement —
see [DESIGN.md §5](DESIGN.md#5-hook-surface)) is **independent re-read
verification** by this plugin. That requires plugins to access
Curator's plugin manager from inside a hookimpl, which is not yet
plumbed; v0.1.0 ships with strict-mode refusal as the only enforcement
mechanism. Future versions will add the re-read verification once the
Curator-side plumbing exists.

## Installation

This plugin requires Curator >= 1.1.1 (which ships the
`curator_source_write_post` hookspec). Note that there is an unrelated
PyPI package named `curator` (Elasticsearch Curator, Python-2 era) that
will conflict if you `pip install curator`; install Jake's Curator from
its source repo:

```bash
# Install Curator from local source (editable for development)
pip install -e /path/to/Curator

# Install this plugin
pip install -e /path/to/curatorplug-atrium-safety
```

Curator auto-discovers the plugin via setuptools entry point at runtime;
no further configuration needed.

## Configuration

| Variable | Values | Default | Effect |
|---|---|---|---|
| `CURATORPLUG_ATRIUM_SAFETY_STRICT` | `1`, `true`, `yes`, `on` (case-insensitive) | unset | Enables strict mode |

When strict mode is enabled, the plugin refuses any write where the
source-side `src_xxhash` is `None` (verification was skipped). When
strict mode is off (default), the plugin observes events but never
refuses.

```powershell
# Enable strict mode for one Curator invocation (PowerShell)
$env:CURATORPLUG_ATRIUM_SAFETY_STRICT = "1"
curator migrate --src-source-id local --src-root C:\Music --dst-source-id gdrive --dst-root MyFolder
```

### What strict mode looks like when it refuses a write

In strict mode, the plugin can refuse a migration in two distinct ways. Each is logged with a clear message that explains exactly which compliance condition tripped:

**Refusal 1: source-side verification was skipped.** Triggered when the migration is run with `verify_hash=False` (e.g., a future `--no-verify-hash` CLI flag). The post-write hook receives `src_xxhash=None`; in strict mode this is a constitutional violation:

```
MigrationOutcome.FAILED
error: ComplianceError: compliance: write proceeded without source-side
hash verification (src_xxhash is None) while strict_mode=True. Atrium
Constitution Principle 2 (Hash-Verify-Before-Move) requires verification;
refusing the write.
```

**Refusal 2: independent re-read mismatch (v0.2.0+, requires Curator >= 1.1.2).** The plugin re-reads the destination via `curator_source_read_bytes` after the migration's own verify succeeds and recomputes the hash. If the recomputed hash differs from `src_xxhash`, that's evidence the source plugin returned different bytes on different reads (non-deterministic plugin bug, transient I/O issue, or misbehaving plugin):

```
MigrationOutcome.FAILED
error: ComplianceError: compliance: independent re-read verification
FAILED. expected xxh3=a1b2c3d4..., actual xxh3=ff00ff00.... This
indicates the source plugin returned different bytes on a subsequent
read than it did on the migration's verify read -- likely a
non-deterministic plugin bug, transient I/O issue, or (worst case) a
misbehaving plugin. Atrium Constitution Principle 2 (Hash-Verify-
Before-Move) requires consistent verification. Refusing the write.
```

In both cases the source bytes are untouched, the FileEntity index continues pointing at the source, and the migration as a whole continues processing other files. The user can re-run with strict mode disabled if they trust the source, or fix the underlying source-plugin issue and retry.

## Module structure

- `curatorplug.atrium_safety.exceptions` — `ComplianceError`
- `curatorplug.atrium_safety.verifier` — pure xxh3_128 utilities
  (`compute_xxh3`, `verify_xxh3`)
- `curatorplug.atrium_safety.enforcer` — decision logic
  (`EnforcementVerdict`, `EnforcementDecision`, `decide`, `enforce`)
- `curatorplug.atrium_safety.plugin` — the Pluggy `@hookimpl` class
  `AtriumSafetyPlugin` plus the module-level `plugin` instance that the
  setuptools entry point references

The verifier and enforcer modules are pure (no I/O, no Curator imports)
and trivially unit-testable. The plugin module is the integration
boundary that ties the decision to the actual Pluggy hook.

## Testing

```bash
cd /path/to/curatorplug-atrium-safety
pip install -e .[dev]
pytest
```

The unit tests (45 tests, ~1s wall clock) exercise the plugin's
hookimpl method directly and cover:

- Pure verifier correctness (xxh3_128 computation, case-insensitive
  comparison, empty-input handling)
- Decision logic (compliant/non-compliant inputs in both modes,
  `written_bytes_len < 0` always-refuse path)
- Enforcement raise behavior (each verdict)
- Env var detection for strict mode (every parametrized truthy /
  falsy / unset case)
- Plugin construction precedence (explicit `strict_mode` kwarg
  overrides env var)
- Hookimpl behavior (compliant write doesn't raise, skipped verify
  raises in strict but not in lax, refusal message includes
  Constitution Principle 2 by name, `written_bytes_len < 0` always
  raises)

Pluggy plumbing integration via a real Curator runtime is covered by
the **P3 integration tests** (separate session — not yet shipped).

## Documents

- [`DESIGN.md`](DESIGN.md) — full design with the four ratified DMs
- [`CHANGELOG.md`](CHANGELOG.md) — version history

## Companion repos

- [`KULawHawk/Curator`](https://github.com/KULawHawk/Curator) — the
  host application this plugin attaches to
- [`KULawHawk/Atrium`](https://github.com/KULawHawk/Atrium) (pending
  push) — the governance layer whose Constitution Principle 2 this
  plugin defends

## License

MIT
