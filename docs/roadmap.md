# Roadmap: known limitations and deferred work

Things deliberately not done, decided (2026-09-27) rather than left ambiguous, so a future
contributor doesn't have to re-derive whether each one was an oversight or a choice.

## Skipped, not planned

**Light theme.** UI_REDESIGN.md §3.1 marked this optional for phase UI-6. Decision: skip it fully.
Dark is the only theme, indefinitely, unless a real user need for a light theme comes up. Nothing
in the codebase is left half-wired for it beyond tokens.css's own comment noting where a
`[data-theme="light"]` block would go.

## Future improvements

**Per-cluster supply-ring slices.** The supply ring (`app/web/charts.py::ring_slices`) shows one
combined "Bundle" slice instead of one slice per cluster, because clusters have no stable identity
across snapshots -- a cluster detected today and a cluster detected in tomorrow's snapshot aren't
linked by anything, so there's no honest way to say "this slice grew" or to split today's combined
bundle percentage back out per cluster without inventing a wallet-overlap matching heuristic.
Building this for real would mean:
- giving each cluster a persistent identity (likely: match clusters across snapshots by wallet-set
  overlap above some threshold, then carry a stable ID forward);
- storing each cluster's own supply percentage per snapshot (today only the aggregate
  `bundle_supply_pct` is stored);
- updating `ring_slices()` to emit one slice per live cluster instead of one combined slice.

This is real pipeline work, not a UI tweak -- worth doing if the bundle-detection side of the
product gets more investment, not a quick follow-up.

**Visual regression baselines.** `tests/ui/` (added in UI-6) captures screenshots and runs
axe-core, but deliberately does not do pixel-diff comparison against committed baseline images.
See `docs/ui-testing.md` for the full reasoning -- in short, the project has no established
workflow for who approves a new baseline when a design change is intentional, and building that
review process is worth doing separately from building the screenshot capture itself.

**Database migration tooling.** `app/storage/db.py::init_db` only calls
`Base.metadata.create_all(engine)`, which creates missing tables but never alters an existing
table (adds a column, changes a type, etc.). Every UI phase that needed a new *value shape* inside
an existing JSON column (e.g. `Reason` objects instead of plain strings in UI-4, `circulating_supply`
added to the smart_money blob in UI-5) could do that safely, because SQLite's JSON columns don't
enforce a schema on their contents. But a genuinely new *column* (for example, a real stored
`supply_breakdown` per snapshot, see the previous item and UI-5's PR) would break the live
production SQLite database on the next deploy, since there is no migration step that runs before
the new code starts reading/writing it.

If the schema needs to grow, add real migration tooling (Alembic is the natural choice for
SQLAlchemy) before adding the column, rather than continuing to work around it with render-time
computation from existing fields.
