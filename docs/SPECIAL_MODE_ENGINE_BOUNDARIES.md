# Special Mode Engine Boundaries

## Non-negotiable rule

WinCro has two independent special-mode algorithms:

- `wongak_legacy_v1`: protected Wongak Factory behavior
- `akgui_v2`: Akgui Factory behavior

Do not select behavior from plan names, waypoint counts, map size, route-start
presence, or map-lock state. Runtime dispatch uses only
`GameModeConfig.engine_profile`.

Coordinate special mode must run through `GameModeDialog`. `RuleExecutor` and
`ActionPlayer` are intentionally fail-closed for direct coordinate special-mode
execution so the obsolete simplified engine cannot become a third behavior
path.

## Ownership

- Wongak algorithm changes belong only to the Wongak legacy runner.
- Akgui algorithm changes belong only to `src/player/special_mode/akgui_v2.py`.
- A change requested for one profile must not modify the other profile.
- Shared code is limited to device infrastructure: OCR capture, input delivery,
  UI event delivery, and immutable data contracts.
- Obstacle, mapping, route recovery, waypoint completion, and transition policy
  are algorithm behavior and must not be added to shared UI/runtime helpers.

The frozen Wongak method hash and ownership are recorded in
`src/player/special_mode/ENGINE_OWNERSHIP.json`. Update that hash only when the
user explicitly requests a Wongak algorithm change. An Akgui-only task must
leave it untouched.

## Map isolation

Map fallback must never cross an engine profile or another rule id. Only a
legacy root map whose filename starts with the exact current rule-id prefix may
be used as a read-only compatibility source. Locked maps and normal playback
must never write map data.

## Required agent checks

Before editing special mode:

1. Read this file and `wincro-special-mode-regression/SKILL.md`.
2. State which profile owns the requested change.
3. Reject a patch that adds plan-name branching to shared runtime code.
4. Run `tests/test_special_mode_engine_isolation.py`.
5. Run the profile-specific regressions and then the full test suite.
6. Report whether any shared file changed and why it was unavoidable.

## Release gate

An Akgui-only change is blocked if the Wongak engine source hash, Wongak plan
profile assignments, or Wongak map files change. The reverse rule also applies.
