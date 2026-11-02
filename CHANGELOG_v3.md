## v3 2026-10-20
- Introduced fast indexed single-pass scan of PLCopen LD comparators and numeric MOVE/ST assignments, avoiding v2 deep graph traversal timeout on 72 MB test XML.
- Generic controller discovery based on observed multiple numeric state comparisons, not `AutoStep` or hard-coded station names.
- Exposed all endpoint hypotheses distinctly as `adjacency_hypothesis`/`reset_hypothesis` rather than presenting them as verified transitions.
- Added independent benchmark with exact vs both-state-anchor metrics and row-by-row misses.
- Tested transfer-plate XML directly; replayed ultrasonic **cached evidence** because its original XML was unavailable for a fresh rerun.
- WARNING: action coverage remains unverified. This is an improvement to coverage observability, not a promise of 100% recall.
