# curatorplug-atrium-safety changelog

All notable changes documented here. Format inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with semver
versioning where reasonable.

## [0.1.0] — 2026-05-08 — First stable release (Session P3)

**Headline:** v0.1.0a1 → v0.1.0 (drop alpha suffix). All 53 tests
passing (45 unit + 8 integration). Curator-runtime integration tests
added; refusal-on-non-compliance proven end-to-end through a real
``build_runtime`` invocation.

### Added

- **`tests/integration/test_curator_runtime.py`** — 8 integration tests
  exercising the full end-to-end flow (real `CuratorRuntime`, real
  pluggy plugin manager, real cross-source migration). Coverage:
  - **`TestPluginAutoDiscovery`** (2 tests): plugin auto-registers via
    setuptools entry point during ``build_runtime``; the registered
    plugin is an instance, not the class (regression-guards the P2
    entry-point fix).
  - **`TestLaxModeDoesNotInterfere`** (2 tests): default lax mode
    doesn't refuse legitimate migrations; even ``verify_hash=False``
    migrations succeed in lax mode.
  - **`TestStrictModeAllowsCompliantWrites`** (1 test): strict mode
    allows migrations where source-side verify happened normally
    (`verify_hash=True`, the default).
  - **`TestStrictModeRefusesNonCompliantWrites`** (2 tests): the
    headline tests — strict mode + `verify_hash=False` triggers
    `ComplianceError`, propagates to `MigrationOutcome.FAILED`, and
    leaves the index + source bytes intact (no corruption under
    refusal).
  - **`TestCoexistenceWithOtherHookimpls`** (1 test): the plugin
    coexists with other registered hookimpls; all fire on each
    `curator_source_write_post` event.

### Changed

- Version `0.1.0a1` → `0.1.0` in `pyproject.toml` and
  `src/curatorplug/atrium_safety/__init__.py`.
- Development status classifier `3 - Alpha` → `4 - Beta` (the plugin
  has Curator-runtime integration coverage now; pre-1.0 because the
  plumbing for independent re-read verification is still pending a
  Curator-side hookspec amendment).
- README status line updated to v0.1.0.

### Verified at release

- Plugin's full test suite: 53/53 pass (~3s wall-clock).
- Curator regression with this plugin auto-discovered:
  `tests/unit/test_migration_cross_source.py` 22/22 pass; full
  migration + GUI slice 342/342 pass (verified earlier in P2).
- Entry-point auto-discovery: confirmed `ep.load()` returns an
  `AtriumSafetyPlugin` instance (the v0.1.0 release commit guards the
  P2 entry-point fix via `TestPluginAutoDiscovery`).
- Strict-mode refusal: confirmed end-to-end. The exact path:
  1. User passes `verify_hash=False` to `MigrationService.apply()`
     (or sets `verify_hash` to False via the CLI's `--no-verify-hash`
     flag in a future release that exposes this).
  2. `_cross_source_transfer` writes the bytes successfully but
     skips its own verify; passes `src_xxhash=None` to the post-write
     hook.
  3. The plugin's `decide()` sees `src_xxhash is None` AND
     `strict_mode=True`, returns `EnforcementVerdict.REFUSE` with a
     message naming Atrium Constitution Principle 2.
  4. `enforce()` raises `ComplianceError`.
  5. `MigrationService`'s outer exception-boundary catches and turns
     the move into `MigrationOutcome.FAILED` with the
     `ComplianceError` message (including "Constitution Principle 2"
     and "Hash-Verify-Before-Move") in `MigrationMove.error`.
  6. The index is NOT updated; the source file is NOT trashed; the
     dst file (already written) is leaked but harmless (next scan
     picks it up as a duplicate).

### Test pollution bug found and fixed during P3

The initial integration tests used `Path(rt.config.db_path).parent /
"src_X"` to construct paths, which on first inspection looks safe but
actually resolves to the **production Curator data directory**
(`%LOCALAPPDATA%\\curator\\` on Windows) — NOT the per-test `tmp_path`.
First test run worked (fresh prod dir); subsequent runs failed because
leftover dirs caused `MigrationOutcome.SKIPPED_COLLISION` instead of
the expected MOVED/FAILED outcomes. Fixed by accepting `tmp_path` as a
fixture parameter on each test method and using it directly. Pytest
automatically gives fixture and test the same `tmp_path` instance per
test function.

## [0.1.0a1] — 2026-05-08 — Initial scaffolding (Session P2)

**Headline:** First package release. Strict-mode refusal + lax-mode
observation against `curator_source_write_post` (Curator v1.1.1+ hook).
Independent re-read verification is deferred until a Curator-side
mechanism for plugins to access the plugin manager from inside a
hookimpl is plumbed (see DESIGN.md §5).

### Added

- `curatorplug.atrium_safety.exceptions.ComplianceError` — soft
  enforcement exception (raised when a write violates Atrium
  Constitution Principle 2; propagates through Curator's exception
  boundary to turn the migration into `MigrationOutcome.FAILED` with
  the refusal reason in `MigrationMove.error`).
- `curatorplug.atrium_safety.verifier` — pure xxh3_128 utilities
  (`compute_xxh3`, `verify_xxh3`). Case-insensitive comparison,
  empty-input handling.
- `curatorplug.atrium_safety.enforcer` — decision logic
  (`EnforcementVerdict.{OK,SKIPPED,WARN,REFUSE}`,
  `EnforcementDecision`, `decide(...)`, `enforce(decision)`). Pure
  functions; no I/O, no Curator imports.
- `curatorplug.atrium_safety.plugin.AtriumSafetyPlugin` — the
  Pluggy `@hookimpl` class. `curator_source_write_post` hookimpl
  invokes `decide` then `enforce`; logs the decision at debug level.
- `curatorplug.atrium_safety.plugin.plugin` — the module-level
  instance referenced by the setuptools entry point.
- `pyproject.toml` — package metadata, setuptools entry point under
  `[project.entry-points.curator]`, dependencies (pluggy >= 1.3,
  xxhash >= 3.0, loguru >= 0.7). The `curator` runtime dependency is
  intentionally NOT declared because of a PyPI name collision with
  Elasticsearch Curator (Python-2-era package); install Jake's Curator
  from local source. See README.md for the install workflow.
- 45 unit tests (`tests/unit/test_verifier.py`,
  `tests/unit/test_enforcer.py`, `tests/unit/test_plugin.py`) covering
  the pure modules + hookimpl behavior in isolation.
- `DESIGN.md` v0.2 RATIFIED (4/4 DMs ratified 2026-05-08): soft
  enforcement (DM-1), single algorithm xxh3_128 (DM-2), Curator's
  existing audit log via `actor='curatorplug.atrium_safety'` (DM-3),
  setuptools entry-point registration (DM-4).

### Known limitations (deferred to future versions)

- **No independent re-read verification.** Plugins don't have clean
  access to Curator's plugin manager from inside a hookimpl, so the
  plugin can't itself call `curator_source_read_bytes` to re-verify
  the destination. This is the most useful feature this plugin would
  eventually offer; it requires a Curator-side hookspec amendment
  (e.g., a `curator_plugin_init(pm)` hook fired at startup that lets
  plugins save a reference). Tracked for a future release.
- **No audit log writes from the plugin itself.** DM-3 ratified using
  Curator's existing audit log via `actor='curatorplug.atrium_safety'`,
  but the plugin currently has no clean access to Curator's
  `AuditRepository` from inside a hookimpl. Same plumbing gap as the
  re-read verification above; same future-release plan.
- **No Curator-runtime integration tests.** P2 ships unit tests only.
  P3 (separate session) adds integration tests that exercise the full
  Curator runtime with this plugin auto-discovered, including a
  deliberately-misbehaving fake source plugin proving end-to-end
  enforcement.

### Bug found and fixed during P2

The initial entry-point declaration pointed at the class
(`AtriumSafetyPlugin`). Pluggy's
`load_setuptools_entrypoints` calls `ep.load()` and registers the
result directly — for a class, that means the class itself gets
registered, not an instance. With a class registered, pluggy's
hookimpl invocations don't auto-bind `self`, and pluggy's
argument-filling collapses by one slot, producing a TypeError like
"missing 1 required positional argument: 'written_bytes_len'". The
fix: add a module-level instance (`plugin = AtriumSafetyPlugin()` at
the bottom of `plugin.py`) and point the entry point at THAT. After
the fix, Curator's regression slice is fully green (342/342) with
this plugin auto-discovered alongside the core plugins.

### Cross-references

- `DESIGN.md` v0.2 — full design + ratified DMs
- `KULawHawk/Curator` v1.1.1 (commit 75dc010, tag v1.1.1) — the host
  release that ships the `curator_source_write_post` hookspec
- `KULawHawk/Atrium/CONSTITUTION.md` Principle 2 — the invariant this
  plugin defends
