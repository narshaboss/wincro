# WinCro Agent Rules

## AI draft authoring

Read `docs/AI_PLAN_EDITING_OPERATOR.md` before using the draft bridge.
The connected AI agent must analyze recording frames and input context and
interpret the user's goal, actions, rules, and expected results. WinCro only
provides media, applies bounded edits, and checks mechanical integrity.
Never substitute input-log replay or automatic action extraction for AI analysis.
Record concise evidence-backed observations and purposes, not hidden reasoning.
After editing, the agent must repeatedly alternate video-to-plan and plan-to-video
cross-checks. Correct findings and redo both reviews after any edit. Recording
two clean passes is a minimum traceability gate, never proof of perfection.
Recording authoring is autonomous: do not interrupt with clarification or data
consent dialogs. Reproduce the observed task order, approximate pacing and result,
not incidental recorder start/stop UI or every transient IME composition glyph,
unless those are the actual requested task. Explain the evidence-backed task boundary.
Preserve visible typing and action gaps with measured video timestamps. Never
replace gradual typing with bulk paste merely because the final text matches.
typing_delay is ignored by the legacy player when typing_random=false. New
recording jobs use opt-in typing_timestamps and per-step timing evidence; do not
change legacy input behavior globally. State video-frame precision and distinguish
measured source timing from real device/recognition latency.
Unobservable essential behavior must remain
an explicitly incomplete result, not a guessed successful plan. Analyze authorizes
only the selected recording and its explicitly associated input log; never scan
for unrelated files or remove existing private intervals. In the recording tab,
Analyze authorizes automatic registration of one NEW plan after current analysis,
crosschecks and durable save verification. Only the app owner publishes; AI tools
remain draft-only. Never change existing plans/playlists or auto-execute the result.
Record automatic publication user_reviewed=false. The legacy plan field
user_verified=true is required for player listing and means registration permission,
not human review or successful playback. Do not change the player filter. Manual bridge
publication still requires explicit owner approval. An analysis record is traceability, not proof of
understanding or successful recognition/playback.

## Recognition baseline

Before changing recognition or investigating an update regression, read
`docs/VIDEO_RECOGNITION_BASELINE.md`. Preserve the documented 309/310
comparison and dedicated-video opt-in boundary. Do not change ordinary image
thresholds, regions, color checks, or general video sampling to fix a dedicated
video action. Reproduce both paths and report field limitations explicitly.

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
