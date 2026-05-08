# `curatorplug-atrium-safety` — Design

**Status:** v0.2 — RATIFIED 2026-05-08; implementation cleared to begin. Original v0.1 was a design proposal; Jake ratified all 4 DM recommendations as written on 2026-05-08. P1 (Curator-side `curator_source_write_post` hookspec) lands as Curator v1.1.1; P2 + P3 land in this package.
**Date:** 2026-05-08
**Authority:** Subordinate to Atrium `CONSTITUTION.md`. Implements Constitution Principle 2 (Hash-Verify-Before-Move) as a *cross-cutting enforcement layer* over Curator's plugin ecosystem.
**Companion documents:**
- `Atrium\CONSTITUTION.md` — supreme authority. Principle 2 is the invariant this plugin defends.
- `Atrium\NAMES.md` — establishes the suite's plugin naming convention (`curatorplug-*` distribution name, `curatorplug.*` Python namespace).
- `Curator\src\curator\plugins\hookspecs.py` — the plugin hook surface this package consumes (`curator_source_write`, `curator_source_read_bytes`, `curator_source_delete`) and contributes to (`curator_audit_event` if added — see DM-3).

---

## 1. Scope

### 1.1 What the plugin IS

`curatorplug-atrium-safety` is a Curator plugin package whose single job is to make Atrium Constitution Principle 2 — **Hash-Verify-Before-Move** — enforceable across the *entire* Curator runtime, including third-party source plugins that Curator core has no compile-time relationship with.

Today (v1.1.0), Hash-Verify-Before-Move is enforced internally by `MigrationService` for every migration path it owns (same-source `shutil.copy2`, cross-source via `curator_source_write` + `curator_source_read_bytes` + re-stream verify). But:

1. There is no enforcement that a *third-party* source plugin's `curator_source_write` implementation actually performs hash-verification on its own. A bad write hook could return success without verifying — and Migration's re-read verify catches *transport* corruption but assumes the hook honored Principle 5 (Atomic Operations) too.
2. There is no retrospective check that every successful migration in the audit log has a corresponding hash-verify event. Bypasses (e.g. a non-Migration code path that calls `curator_source_write` directly) would not be detected.
3. There is no constitutional-compliance status visible to the user — no answer to "are all my installed plugins Atrium-compliant?".

This plugin closes those three gaps. It is small, focused, and entirely additive to Curator core.

### 1.2 What the plugin is NOT

- **Not** a replacement for Curator's internal Migration discipline. Migration continues to do hash-verify-before-move itself; the plugin is a *belt-and-suspenders* layer, not a substitute.
- **Not** a general-purpose policy engine. It enforces Principle 2 specifically. Other constitutional principles (Reversibility, Plan/Apply, No Silent Failures, Atomic Operations) get their own dedicated plugins if and when needed (`curatorplug-atrium-reversibility`, etc.) — out of scope for this package.
- **Not** a mandatory dependency of Curator. Users who don't install it get current behavior. Curator core never imports `curatorplug.atrium_safety` directly; the plugin discovers itself via setuptools entry points.
- **Not** a replacement for Atrium itself. Atrium ratifies the principle; this plugin enforces it. The principle's authority comes from `Atrium\CONSTITUTION.md`; the plugin's enforcement comes from this package.

---

## 2. Invariants the plugin must preserve

Every Phase 2-style invariant listed in `Curator\docs\TRACER_PHASE_2_DESIGN.md` §2 stays preserved. Specifically:

1. **`curator_id` constancy** — the plugin must not invalidate file identity by side-effecting the `FileEntity` row.
2. **No Silent Failures** — if the plugin detects a violation, it MUST surface it through an explicit channel (audit event, CLI exit code, GUI dialog) rather than logging-and-forgetting.
3. **No Plugin-Side Mutations Without Plan/Apply Gate** — if DM-1 lands as "enforcement" mode (refusal), the refusal must happen *before* any mutation, not after partial mutation.
4. **Backward Compatibility** — installing this plugin must not break a working Curator installation. Worst case at install time: warnings about non-compliant plugins; best case: silent operation.

---

## 3. DM resolutions (require Jake's ratification)

### DM-1 — Enforcement mode

**Question.** When the plugin detects a non-compliant write hook, what does it do?

**Options:**
- **(a) Advisory** — log a warning via `loguru`, write an `audit.compliance_warning` audit entry, but allow the operation to proceed.
- **(b) Soft enforcement** — refuse the operation by raising `ComplianceError`. Curator's existing exception-boundary handling in `MigrationService` turns this into a `MigrationOutcome.FAILED` per file. Reversible: uninstall the plugin to bypass.
- **(c) Hard enforcement** — refuse the operation AND mark the offending plugin as `blocked` in a runtime registry, refusing all further calls to its hooks until manually unblocked.

**Recommendation: (b) Soft enforcement.**

Rationale: Advisory (a) is too easy to ignore — log noise gets filtered out, audit warnings are reviewed quarterly at best. Hard enforcement (c) is too aggressive for a plugin: blocking a legitimate-but-momentarily-misbehaving plugin (e.g., a transient network error during verify) requires a human to intervene to unblock, which violates Principle 1 (Reversibility) at the operational level.

Soft enforcement (b) hits the sweet spot: failed operations are *visible* (status flips to FAILED, audit event written, CLI exit code non-zero, GUI shows the failure in the Migrate tab), but recovery is automatic (next call to the same plugin gets a fresh chance; uninstalling the safety plugin disables enforcement entirely).

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake ("continue" = ratify per the explicit turn-default convention). Implementation cleared.

### DM-2 — Hash algorithm scope

**Question.** Curator uses `xxhash3_128` everywhere as the canonical hash for file identity. Should this plugin verify with the same single algorithm, or add a second algorithm (e.g. `sha256`) as a belt-and-suspenders check against xxh3 collisions or implementation bugs?

**Options:**
- **(a) Single algorithm (xxh3_128 only)** — matches Curator's existing internal verify exactly. Plugin re-verifies what Curator already verifies. The plugin's value-add becomes "verify that the verify happened" rather than "additional cryptographic confidence."
- **(b) Dual algorithm (xxh3_128 + sha256)** — plugin computes both. xxh3 is fast and matches Curator's internal hash; sha256 is slower but cryptographically stronger and immune to xxh3-specific implementation bugs. Both must match.
- **(c) Single, but configurable** — default xxh3, with a config flag to opt into dual-algorithm mode for paranoid deployments.

**Recommendation: (a) Single algorithm (xxh3_128).**

Rationale: xxh3_128 is non-cryptographic but *collision-resistant for non-adversarial inputs* (~2^64 collisions expected; practically unreachable for personal-scale corpora). The threat model the plugin addresses is **misbehaving plugins**, not **adversarial attackers**. A plugin author who wants to hide a bad write doesn't need to engineer a hash collision; they just need to skip verification. So dual-algorithm protection (b) defends against a threat we don't have, at meaningful CPU cost (sha256 is ~5-10x slower than xxh3 on modern CPUs).

If a future deployment scenario emerges (e.g., shared multi-tenant Curator hosting where adversarial plugins are a real concern), DM-2 can be revisited — but for solo / small-team Curator use, single-algorithm is honest.

(c) is overengineering: a config flag we never flip is dead code.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Single algorithm (xxh3_128); revisit only if shared-tenancy threat model emerges.

### DM-3 — Audit channel

**Question.** When the plugin detects a violation, where does the record live?

**Options:**
- **(a) Curator's existing audit log** — write `AuditEntry(actor='curatorplug.atrium_safety', action='compliance.violation', ...)`. Same channel as `migration.move`, `trash.send`, etc. Queryable through existing audit-log tools.
- **(b) Plugin-private log table** — new SQLite table `compliance_events` owned by this plugin. Migration-002-style schema add. Separates compliance events from operational audit events.
- **(c) Both** — write to audit log for visibility, also keep a private table for compliance-specific queries (e.g., "show me all non-compliant plugins this quarter").

**Recommendation: (a) Curator's existing audit log.**

Rationale: Audit log entries are already the canonical "something significant happened" channel. Adding a parallel log fragments the user's mental model. The audit-log table has no schema obstacle to recording compliance events — `details_json` is freeform, and the existing audit-log GUI tab will display them in the same chronological view as everything else. Plugin-private (b) creates a second source of truth users would have to remember exists. (c) is (a) plus dead-weight (b).

If the audit log volume becomes overwhelming (hundreds of compliance events per day from a misbehaving plugin), DM-3 can be revisited — but the plugin should be soft-enforcing those operations to FAILED anyway (DM-1), so volume should be tiny in practice.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Use Curator's existing audit log via `actor='curatorplug.atrium_safety'`.

### DM-4 — Plugin registration mechanism

**Question.** How does Curator discover this plugin?

**Options:**
- **(a) Setuptools entry point** — the plugin's `pyproject.toml` registers itself via `[project.entry-points.curator]` exactly like the existing `curatorplug.example` placeholder in Curator's pyproject. User installs `pip install curatorplug-atrium-safety` and Curator auto-discovers on next launch.
- **(b) Explicit config import** — user adds the plugin's import path to `curator.toml` (or equivalent config file). Curator only loads what's listed.
- **(c) Both, with explicit config taking priority** — entry-point auto-discovery for ergonomics, config override for advanced users.

**Recommendation: (a) Setuptools entry point.**

Rationale: This is exactly what the placeholder comment in Curator's `pyproject.toml` (`example_plugin = "curatorplug.example:Plugin"`) was designed for. The pluggy framework Curator uses already handles entry-point discovery transparently. Going with (a) means: install the package, it works; uninstall, it's gone. No config file edit required. This is the standard Python plugin pattern and Curator already plumbs for it.

(b) and (c) add complexity for a problem Curator doesn't have (single-user installations don't need fine-grained plugin loading control).

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Setuptools entry point under `[project.entry-points.curator]`.

---

## 4. Module structure

```
curatorplug-atrium-safety/
├── pyproject.toml                          # name=curatorplug-atrium-safety,
│                                           # entry-point under [project.entry-points.curator]
├── README.md                               # what + why + install
├── CHANGELOG.md                            # release history (v0.1.0 = MVP)
├── DESIGN.md                               # this file
├── src/
│   └── curatorplug/
│       └── atrium_safety/
│           ├── __init__.py                 # __version__, public API
│           ├── plugin.py                   # the Pluggy hookimpl class
│           ├── enforcer.py                 # prospective checks (DM-1 soft enforcement)
│           ├── verifier.py                 # the actual hash-verify implementation (DM-2 single xxh3)
│           └── audit.py                    # AuditEntry construction (DM-3 use existing log)
└── tests/
    ├── conftest.py                         # fixtures: mock plugin manager, mock audit repo
    ├── unit/
    │   ├── test_enforcer.py                # behavior of the enforcement decisions
    │   ├── test_verifier.py                # hash computation + comparison
    │   └── test_audit.py                   # audit entry shape
    └── integration/
        └── test_with_curator.py            # plugin loaded into a real CuratorRuntime,
                                            # exercise via a deliberately-misbehaving fake source
```

`src/curatorplug/atrium_safety/__init__.py` exports nothing user-facing beyond `__version__` — the plugin is invisible at the import level. All wiring happens through pluggy's entry-point discovery.

`src/curatorplug/` is a **namespace package** (no `__init__.py` at the `curatorplug/` level). This lets future plugins like `curatorplug-atrium-reversibility` install side-by-side under the same `curatorplug.*` namespace without conflict.

## 5. Hook surface

The plugin contributes implementations of one Curator hook and consumes another:

| Hook | Direction | Contract |
|------|-----------|----------|
| `curator_source_write_post` | **NEW hookspec proposed in Curator** | Called after any plugin's `curator_source_write` succeeds. Receives `(source_id, file_id, src_xxhash, written_bytes_len)`. The atrium_safety plugin re-reads via `curator_source_read_bytes`, recomputes xxh3, compares. On mismatch: raises `ComplianceError` (DM-1 soft enforcement), writes audit entry (DM-3). |
| `curator_audit_entry` | consumed | The plugin writes `AuditEntry(actor='curatorplug.atrium_safety', action='compliance.violation' | 'compliance.verified', ...)` through the existing AuditRepository. |

**Curator-side change required:** add the `curator_source_write_post` hookspec to `src/curator/plugins/hookspecs.py` and have `MigrationService` (or a new central wrapper) invoke it after each successful `curator_source_write`. This is a small (~30 LOC) Curator-core change that lands as a separate commit before this plugin can install. **Tracked as a prerequisite, not part of this plugin's scope.**

Alternative: do the verification entirely from the plugin side by hooking into the audit log (post-fact reconstruction). Less elegant; more work. Going with the new hookspec.

## 6. Implementation plan

Three sessions, each ending with a clean commit:

| Session | Scope | LOC | Tests | Hours |
|---------|-------|-----|-------|-------|
| **P1: Curator-side prerequisite** | Add `curator_source_write_post` hookspec to Curator. Wire `MigrationService._cross_source_transfer` and `_execute_one_same_source` to invoke it on success. Lands in Curator as a separate commit, version bump to v1.1.1. | ~80 | ~10 | 1.0h |
| **P2: Plugin scaffolding + verifier + enforcer** | Package skeleton, `pyproject.toml` with entry-point, `verifier.py` (xxh3 compute + compare), `enforcer.py` (decision logic per DM-1), `audit.py` (AuditEntry construction per DM-3), unit tests. | ~250 | ~20 | 1.5h |
| **P3: Integration tests + docs + release** | Integration test with a deliberately-misbehaving fake source plugin to prove enforcement works end-to-end. README, CHANGELOG.md, v0.1.0 release ceremony (tag, build wheel). | ~100 + docs | ~5 | 1.0h |
| **TOTAL** | | **~430 LOC + ~120 LOC tests** | **~35 tests** | **~3.5h** |

This sits at the lower end of the original ~3-4h wishlist estimate. The scope is genuinely small because we're standing on a lot of existing infrastructure (pluggy, audit log, xxhash, the new write_post hook).

## 7. What does NOT ship in v0.1.0

- **Reversibility enforcement** (Constitution Principle 1). Out of scope; would be `curatorplug-atrium-reversibility` if it exists.
- **Plan/Apply enforcement** (Constitution Principle 3). Curator already enforces this via the `--apply` gate.
- **Multi-algorithm hash verify** (DM-2 (b)). Deferred unless a real threat model emerges.
- **Compliance dashboard / report CLI** (`curator audit-compliance --since 30d`). Useful, but post-MVP. v0.2.0 candidate.
- **Real-time compliance status in the GUI** (a green/yellow/red badge on the Migrate tab indicating "all plugins compliant"). v0.2.0 candidate.

## 8. Cross-references

- `Atrium\CONSTITUTION.md` Principle 2 — the invariant being enforced.
- `Curator\docs\TRACER_PHASE_2_DESIGN.md` — establishes the per-file Hash-Verify-Before-Move discipline this plugin doubles down on.
- `Curator\src\curator\plugins\hookspecs.py` — hook surface this plugin consumes and (via P1) extends.
- `Curator\src\curator\services\migration.py` — `_cross_source_transfer` and `_execute_one_same_source` are the call sites for the new `curator_source_write_post` hook.
- `Curator\src\curator\storage\repositories\audit_repo.py` — the AuditRepository this plugin writes through.

## 9. Revision log

- **2026-05-08 v0.1** — first issued. Captures: §1 scope (what the plugin is, what it isn't), §2 invariants the plugin must preserve, §3 DM-1 through DM-4 with recommendations awaiting Jake's ratification, §4 module structure (namespace package under `curatorplug.atrium_safety`), §5 hook surface (one new Curator-side hookspec required as a prerequisite), §6 ~3.5h three-session implementation plan, §7 explicit v0.1.0 deferrals, §8 cross-references. No code has been written; no commits have landed. Next step: Jake reviews DMs → ratifies (or modifies) → doc flips to v0.2 RATIFIED → P1 (Curator-side hookspec addition) lands → P2 (plugin scaffolding) lands → P3 (integration + release) lands.
- **2026-05-08 v0.2** — RATIFIED. Jake ratified all 4 DM recommendations (DM-1 through DM-4) as written without modification (replied "continue" against the explicit `ratify`-default convention). Doc status flips from "design proposal" to "approved spec"; P1 implementation cleared to begin. No structural changes to the design — only ratification-status flips on each DM and this revision-log entry.
