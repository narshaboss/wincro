# Recognition Baseline: 309 vs 310

Recorded 2026-10-31 for future update-regression investigations. This is a
versioned source reference, not a promise that every remote PC has installed it.

## Sources and Scope

- Before: public v1.0.309, commit `2b05847c0694025f94d462ba63f499976eff7bf0`.
- After: v1.0.310, recognition implementation reviewed at
  `5bea4fa9f777885ea827a03930d21d6735cb306d`; release metadata changes follow it.
- Only dedicated monitoring action types `동영상클릭` and legacy `동영상 입력`
  opt in, including their repeat-click and video-test paths.
- Ordinary images, ordinary video conditions and monitor-watch images retain
  their existing loaders and recognition behavior. No plan, map, engine,
  threshold, region, scale, color or brightness setting was changed.

## Reproducible Before/After Comparison

Automatic-hunt action 8, `연 자동사냥 결과 찾기`, uses
`template_video_20260703_154056_306.mp4`: 199 frames, 30 fps, 6.633 seconds,
30 pixels wide by 32 pixels high. Its dedicated-video confidence is 0.8,
region `[897, 1, 1715, 795]`, color and brightness checks both ON. The parent
monitor-watch image still requires 95%; that prerequisite is not weakened.

| Measurement | 309 | 310 |
| --- | --- | --- |
| Representative selection | 8 evenly spaced frames | Adaptive shape/color/brightness coverage |
| Representatives for this clip | 8 | 12 |
| Native-size frames below 0.8, best of bank | 20/199 | 0/199 |
| Minimum best-of-bank correlation | 0.6176960468 | 0.9834259152 |

Old indices: `[0, 28, 56, 84, 113, 141, 169, 198]`.
New indices add `[44, 47, 49, 51]` to that bank. The actual runtime matcher,
with existing scales and color/brightness ON, also detects all 199 frames.
The native-size minimum is not a minimum returned click score: runtime returns
the first passing match, not necessarily the maximum.

This reproduces missing phases in the registered clip. No screenshot of the
user's actual missed moment was obtained; do not claim it explains every miss.

## Unchanged Recognition Contract

General video sampling remains capped at 8 frames. Matching remains
`cv2.TM_CCOEFF_NORMED`. Scales remain
`(1.0, 1.1, 0.9, 1.25, 0.8, 1.4, 0.7, 1.5)`. Existing LAB color/brightness
limits remain 18/28. The five general loader/variant/visual-check/resize helpers
were AST-compared against 309. Dedicated opt-in must not leak into normal calls.

## Bounds and Diagnostics

The new bank considers at most 240 candidates / 4,000,000 pixels and retains
at most 32 representatives. Its separate cache is bounded to 4 videos / 16MB;
this is not the whole application's memory cap. Encoded files over 64MB are
rejected before reading for adaptive analysis. Content SHA-256 detects file
replacement even with identical size/mtime. Unsupported or over-budget media
falls back to legacy sampling; invalid/cancelled media must not reuse stale
cached frames. Captures are released on errors and cancellation.

`[동영상진단]` reports real best similarity, threshold, frame count, region and
failure reason. Dedicated diagnostics no longer read template height as a
score (the old tuple indexing could display 3200%). Waiting diagnostics must
not reset progress/stuck timers. No persistent plan-schema option was added;
`dedicated_video_test` is temporary UI-test routing only.

## Evidence and Remaining Risks

[Implementation Windows QA](https://github.com/narshaboss/wincro/actions/runs/41176354953):
32 targeted video/isolation tests; full suite 1,580 passed, 3 opt-in skips,
8 dependency warnings. Native/UI opt-in checks ran separately. Test groups
overlap and must not be summed. Metrics artifact: `dedicated_video_metrics.json`.

More representatives can increase this dedicated action's search cost. Mini-PC
CPU and recognition latency have not been measured in the field. Large or
complex clips can hit budgets/fallback; coverage is not universal. Parent-watch
failure, occlusion, screen position, scaling and changed field colors remain
possible independent causes. Engines and PC7 temporary-wall behavior are not
fixed by this patch.

For a new miss, first record the installed EXE version, exact action identity,
parent-watch entry, screenshot and region, confidence and visual flags, clip
content hash, diagnostic reason/best score, and timing/CPU. Compare this baseline
before changing unrelated recognition. Keep play waiting/notification semantics.

Main-PC input, screen capture, apps and restarts were not used for verification.
Existing updater policy remains package-authoritative for same-named plans/maps;
publication does not prove per-PC installation or preservation of custom maps.
