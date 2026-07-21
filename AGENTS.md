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
