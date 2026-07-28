# WinCro Agent Rules

## Special mode

Before changing special mode, read
`docs/SPECIAL_MODE_ENGINE_BOUNDARIES.md` and the
`wincro-special-mode-regression` skill.

`wongak_legacy_v1` and `akgui_v2` are isolated algorithms. Never implement
profile behavior with plan-name checks or waypoint-shape heuristics. Never
modify both engines for a one-profile request. Do not add cross-profile map
fallbacks. Run `tests/test_special_mode_engine_isolation.py` before reporting
completion.

## Save integrity

Treat every user-facing Save button as a durable save operation, not an
in-memory apply operation. Before reporting any save-related work complete,
verify the full chain: update the in-memory model, write the correct plan JSON
or database record, reload it from disk/database, and confirm that the saved
actions and settings are identical. Never report save success based only on UI
state or serialization unit tests. A nested settings dialog labelled Save must
either persist its parent document immediately or clearly require and preserve
an outer save without data-loss risk.
