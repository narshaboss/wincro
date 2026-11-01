# Recording Analysis Latency Review

## Observed Failure

The selected recording is 9.133 seconds, 2560x1080. The prior job
`job_a27375a1fdd2482db7cc52167058e43b` ran from 11:36:15 to 11:49:35 UTC
on 2026-11-01: approximately 800 seconds, three model runs, 102 MCP calls,
including 44 get_frames calls. It ended needs_review, not completed.

Its analysis treated recording startup/shutdown focus changes and intermediate
Korean IME composition as mandatory workflow steps, pursued unobservable exact
physical keystrokes, and repeated the same unresolved limitations. typing_delay
was requested by the model but rejected by the draft field allowlist.

## Narrow Changes

- Explain evidence-backed task boundaries and semantic text entry to the AI.
  Never silently omit a real action or claim unsupported essential behavior works.
- Batch up to 12 overview frames and four detail frames per call; open one decoder
  per batch and close it on success/error. Keep original hashes, privacy checks,
  original-coordinate crops, trace provenance and transport size limits.
- Include all editable settings in compact action reads and support existing type
  pacing fields. Provide actionable invalid-field errors rather than blind retries.
- Stop another full repair run if plan, analysis coverage and review outcomes have
  not improved. Preserve an incomplete result instead of manufacturing success.
- Record pass timings and local tool counts/times without arguments or credentials.

## Actual GPT Measurement

`tools/benchmark_recording_analysis.py` ran the SAME selected recording, associated
input log and previous job goal using the existing model/runtime settings. It used
only a new temporary plans/templates/job workspace. No desktop input, app restart,
original plan edits or actual playback took place.

The first measured run on `545e728` completed in **226.906 seconds**, one model
pass, 26 MCP calls (including connection_status), six get_frames calls. Four
actions: open Notepad, new tab, focus document, type the final visible greeting.
The two evidence-backed reviews passed and a new temporary plan was written,
reloaded and registered with user_reviewed=false. This is roughly 72% less elapsed
time than the prior recorded run, not a controlled multi-sample benchmark.

Measured local draft/media tool work totals about 2.53 seconds; the remaining
elapsed time includes model inference, communications, MCP round trips and runner
startup. No model or reasoning-level downgrade was used. It still takes minutes,
not seconds, and there is no fixed-time guarantee for other recordings.

Private local evidence is under
`%LOCALAPPDATA%/Temp/wincro-analysis-latency-20261101-a/summary.json` and its
`jobs` / `plans` subdirectories. Recording content and generated private plan
files are not checked into this repository. The source was independently viewed
at task-transition/result frames; actual playback/recognition remains untested.

## Regression Scope

Added offline ten-second-video, decoder-release, privacy, unchanged-stat file
replacement, batch-budget, durable type-settings publication, repair-progress,
and timing/error-metric simulations. Isolated Windows CI also runs the existing
native pipe/UI, full regression and special-mode isolation suites. Test actors
validate mechanics; they do not substitute for the actual GPT measurement above.

No runtime player/recognition/special-mode algorithms or existing user plans were
changed. No release/version deployment is part of this work.
