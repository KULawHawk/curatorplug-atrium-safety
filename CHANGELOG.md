# curatorplug-atrium-safety changelog

All notable changes documented here. Format inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with semver
versioning where reasonable.

## [0.2.0] — 2026-05-08 — Independent re-read verification (PLUGIN_INIT P2)

**Headline:** v0.1.0 → v0.2.0 (minor bump). The plugin now performs
**independent re-read verification** of dst bytes after every successful
cross-source migration, catching cases that Curator's single-shot verify
might miss (non-deterministic source plugins, transient I/O issues,
post-write corruption). Requires Curator >= 1.1.2.

### Added

- **`AtriumSafetyPlugin.curator_plugin_init` hookimpl** — saves the
  pluggy `PluginManager` reference as `self.pm` for later use. Fired
  once at startup by Curator >= 1.1.2's new `curator_plugin_init`
  hookspec. When running against Curator < 1.1.2 (which doesn't fire
  the hook), `self.pm` stays at its constructor default of `None` and
  the plugin gracefully degrades to v0.1.0 behavior.
- **`AtriumSafetyPlugin._verify_via_re_read`** — the core re-read
  verification logic. Calls `self.pm.hook.curator_source_read_bytes(...)`
  in chunks to assemble the full dst content, computes its xxh3_128,
  compares to the `src_xxhash` provided in the post-write hook. Returns
  an `EnforcementDecision` with one of four verdicts: OK (match), OK
  (conservative fallback when re-read can't be performed), REFUSE
  (mismatch in strict mode), or WARN (mismatch in lax mode -- advisory
  only, doesn't refuse).
- **`AtriumSafetyPlugin._read_all_via_hook`** — chunked-read helper
  mirroring `MigrationService._read_bytes_via_hook` so the safety
  plugin's re-read uses the same protocol Curator's own verify does.
- **Two-phase enforcement in `curator_source_write_post`:** Phase 1
  is the existing `decide()` logic (DM-1 of `DESIGN.md` v0.2: refuse
  when `src_xxhash is None` AND strict mode); Phase 2 is the new
  re-read verification (only when `self.pm is not None` AND
  `src_xxhash is not None`).

### Tests (+18 new — 53 → 71 total)

- **`tests/unit/test_re_read_verification.py`** (NEW, 14 tests):
  - `TestReReadHappyPath` (2): match returns OK in both lax + strict
    modes.
  - `TestReReadMismatch` (2): mismatch refuses in strict, warns in
    lax. Refusal message includes both expected + actual hashes plus
    Constitution Principle 2 by name.
  - `TestReReadConservativeFallbacks` (2): no plugin owns the
    source_id → OK; re-read raises an exception → OK (don't refuse a
    write just because re-read itself failed).
  - `TestReReadChunkLoop` (3): short reads signal EOF; multi-chunk
    reads assemble correctly; empty content handled.
  - `TestReReadIntegrationWithCuratorSourceWritePost` (5): no re-read
    when pm is None (backward compat with Curator < 1.1.2); re-read
    happens when pm is set; mismatch raises ComplianceError in strict;
    mismatch doesn't raise in lax; no re-read when src_xxhash is None
    (decide-phase refusal is independent).
- **`tests/integration/test_curator_runtime.py::TestReReadVerificationAutoDiscovered`**
  (4 new integration tests):
  - `test_plugin_pm_is_populated_after_build_runtime`: confirms that
    `curator_plugin_init` actually fires for the auto-discovered
    plugin and the pm reference is saved. Prerequisite for everything
    else.
  - `test_compliant_migration_passes_re_read_in_strict`: a normal
    cross-source migration in strict mode passes re-read verification
    (no false positives from the new code path).
  - `test_re_read_catches_post_write_corruption_in_strict`: **the
    headline test.** A `tryfirst=True` hookimpl corrupts the dst file
    AFTER MigrationService's verify but BEFORE the safety plugin's
    hookimpl. The safety plugin reads via LocalPlugin, sees the
    corrupt bytes, raises `ComplianceError`, MigrationService turns
    the move into FAILED with the refusal reason in error. Proves
    end-to-end that re-read catches what single-shot verify misses.
  - `test_re_read_mismatch_does_not_refuse_in_lax`: same scenario as
    above but in lax mode — mismatch is logged, migration still
    succeeds (advisory mode).

### Changed

- Version `0.1.0` → `0.2.0` (minor bump because behavior changes
  visibly in strict mode: more refusal cases caught now).
- `pyproject.toml` and `__init__.py` `__version__` reflect 0.2.0.

### Backward compatibility

- **Strictly additive when running against Curator 1.1.2+.** Existing
  v0.1.0 enforcement behavior (strict-mode refusal of skipped-verify
  writes) is preserved; the re-read phase is a NEW second layer.
- **Graceful degradation against Curator < 1.1.2.** Without
  `curator_plugin_init` firing, `self.pm` stays None and the re-read
  phase is silently skipped — plugin behaves exactly like v0.1.0.
  Plugin authors should still pin Curator >= 1.1.2 in production for
  the full feature set, but development environments with older
  Curator won't crash.
- **Lax-mode users see no behavior change** in either Curator
  version: lax has always been advisory-only.
- **Strict-mode users on Curator 1.1.2+ see MORE refusals** in cases
  where dst bytes don't match src hash on a re-read — these are real
  compliance violations that v0.1.0 silently allowed. This is the
  intended behavior change and is why this is a minor bump rather
  than a patch.

### Why a minor (0.1.0 → 0.2.0) and not a patch

User-visible behavior CHANGES in strict mode: writes that v0.1.0
allowed (because the source-side verify said OK) may now be refused
if the re-read hash doesn't match. That's intentional -- it's the
headline value of the v0.2.0 feature -- but it's a behavior change,
so minor bump is honest. Lax-mode users see no behavior difference;
they just get warning logs they didn't get before.

### Cross-references

- `Curator/docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md` v0.2 (RATIFIED
  2026-05-08) — the design that motivated this release; this is P2 of
  its 3-session plan. P3 is regression sweep + safety-plugin doc
  updates (DESIGN.md v0.3, README polish).
- `Curator/CHANGELOG.md` `[1.1.2]` — the host release that ships the
  `curator_plugin_init` hookspec this plugin consumes.
- `curatorplug-atrium-safety/DESIGN.md` v0.2 §5 — the deferred
  re-read verification capability whose implementation lands in this
  release. Will be marked IMPLEMENTED in DESIGN.md v0.3 in P3.

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
