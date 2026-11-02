# GPT Live Observation, Experience and Movement Overhaul

## Scope

Application-created GPT Play sessions enable the new policy. Legacy standalone
session adapters retain their old contract for compatibility. Existing plans,
recordings, maps, image/video recognition and both special-mode engines are not
modified. No release, version bump, deployment, main-PC input or firmware upload.

## Implemented

- The default fast path combines screen+memory in play_situation and review+input+
  result screen in play_decide. Two model/tool round trips replace five separate
  calls without removing the durable review or input safety checks.
- Guard/digit PNGs are not sent as base64 text in model receipts. GPT authors
  purpose/result/guards and references a recorded action by request ID; the app
  resolves the exact stored input internally. This is not automatic log extraction.
- Task-wide, stage-ranked retrieval includes older success/failure evidence, not
  only the last ten events. Input requires an actual retrieval and a durably saved
  apply/reject review naming returned references. Resume, retry and reported stage
  changes invalidate review. This records provenance, not proof of understanding.
- Entry-only routine mismatches do not quarantine a valid routine. Result failures
  still do. Old entry-only quarantines are repaired only when the complete stored
  failure count is supported by entry-mismatch events; missing history stays blocked.
- Selected-window background capture targets 4 Hz. A six-frame / 24 MB raw ring
  and six-frame / 12 MB encoded selection buffer are ephemeral. Observations return
  timestamped recent thumbnails plus a current full frame. Cited frames are pinned
  durably before use in input, notes, experience, calibration or crosschecks.
  Old evidence is never deleted. Capture does not mean GPT inference at 4 Hz.
- GPT can calibrate visible XY with separate numeric ROIs and real, labelled digit
  crops. The dedicated reader rejects unseen/ambiguous glyphs, size changes, labels
  and punctuation. Nothing is imported from either special-mode coordinate engine.
- Incremental calibration keeps previously learned digits when geometry and
  threshold settings match, deduplicates crops, and retains old profile versions.
  Changed geometry/threshold requires fresh complete calibration. Failed partial
  calibration does not overwrite the active profile. DB reopen checks cover both
  new and old coordinates after adding one newly observed digit.
- GPT chooses target and axis order. Local navigation reads XY between Arduino GL1
  holds of 50..500 ms, up to four seconds per feedback slice. Arrival requires a
  second screen reading. Blockage, unexpected movement, overshoot, large coordinate
  jump, uncertain digits and scene changes stop the slice; GPT chooses what next.
  No permanent walls, arbitrary pathfinding or invented semantic state.
- The exact navigation profile, scene guard, target and feedback semantics are
  retained inside the shared native gpt_play_behavior action. No flattening into
  naked keys. Saved plan JSON is reloaded through AutomationPlan and compared.
- Goal declaration freezes further game inputs and releases the device. Read-only
  draft authoring and crosschecks remain possible without the game in foreground.
  Drafts may contain unverified routines but cannot be published as verified plans.
  Independent replay is a separate owner-only, one-run UI permission. The existing
  ten-success publication gate remains; missing replay is explicitly incomplete.
- UI reports stage, experience review, latest XY/target/stop reason, evidence size,
  capture time and between-tool time (model plus transport, not pure inference).
  Timings are bounded to 64 samples per category. Stale input is rechecked after
  write-ahead persistence and before any device transmission.
- Completed receipt hot caches are bounded by both count and bytes, not just
  128 entries. Larger navigation profiles cannot silently inflate the session
  beyond its JSON safety cap. Archived request IDs still deduplicate from disk.

## Verification

- Local final-source scoped run: 234 passed in 37.07 seconds. This includes the
  GPT session, learning, timed-input, new live-policy/navigation, recording-job
  compatibility and special-engine isolation suites. Screens/devices were fake;
  no operator desktop or Arduino input was sent.
- A prior 232-test one-process run also checked threads after teardown and found
  no newly surviving threads. This is a teardown check, not a long-duration soak.
- Final-source Windows GPT simulations: 190 passed. Native GPT UI and authenticated
  pipe tests: 15 passed, including both legacy and combined-tool contracts. The
  UI was checked at 100%, 125% and 150% scaling. Generated memory and replay-permit
  screenshots were visually inspected; the two-line permission button fits.
- The first intermediate full regression had one failure: a ten-trial publication
  test exceeded its five-second worker join during a durable-save delay. The
  worker later logged successful completion. Its input/result checks were not
  disabled and its timeout was not relaxed. A subsequent complete run passed
  2,052 tests; the last pre-calibration-correction run passed 2,054 tests. Six
  opt-in native modules were skipped in ordinary collection.
  Those native modules have separate enabled Windows QA steps. This intermittent
  timing failure is retained here rather than presented as an all-green history.
- Final source: c8fda103dac5bc4f181ee25da77e7c893417d147.
  [Windows run 41496025398](https://github.com/narshaboss/wincro/actions/runs/41496025398):
  full regression **2,055 passed, six opt-in modules skipped, eight warnings**
  in 268.00 seconds. Native GPT UI 15, draft UI/pipe 27, actual Codex binary with
  an offline fake model 9, window layout 37 and owned-window input/OS tests 2
  all passed in their separately enabled steps. These counts overlap other
  runs and must not be summed into a unique total. This does not test a live GPT
  model's game understanding. GL1 source compiled and passed fake-hardware tests;
  no physical board was exercised.
- The final CI run's overall badge is **cancelled**, not green: all individual
  steps (including full regression, artifact upload and post-checkout) reported
  success, but final runner cleanup crossed the job's 15-minute cap by 3 seconds.
  Full-suite success was logged at 18:12:40 UTC; post-checkout completed at
  18:12:45 UTC. GitHub explicitly reports the maximum-execution-time annotation.
  The job-level allowance is now 25 minutes; no assertion, individual-test timeout
  or game input limit was relaxed. This workflow-only timeout adjustment was
  checked as a scalar diff, not rerun as another full CI job. Product source is
  unchanged from the tested commit.

## Operation

- Open Screen Recording > GPT Play, enable the existing GPT connection and select
  the game window. Use a new experience or select an existing task and continue it.
- New app-created sessions automatically use this policy; there is no extra
  experience-review checkbox to enable. GPT must retrieve and record its use of
  relevant experience before input. Restart preserves task knowledge, not a
  queued input or authority from a stale screen.
- For tile movement, GPT must first label the visible numeric XY regions and
  provide digit examples from actual frames. Until valid calibration exists,
  coordinate feedback navigation is unavailable; ordinary bounded timed input
  remains available. Unseen digits require additional evidence, not guessing.
- The experience tab exposes the accumulated records. Live status shows review,
  coordinate/target/stop reason, capture and model-plus-transport timings.
- After GPT declares the goal achieved, input freezes while it authors/reviews
  a new candidate. For an independent trial, prepare the starting game screen and
  use Plan Verification > Allow one verification replay. Each permission is
  one-shot; a draft is not a verified published plan.
- Already running source processes must be restarted to load changed modules.
  No app restart, packaged release or remote-PC deployment was performed here.
- CI tests the task's committed branch. Pre-existing uncommitted app/config/plan
  edits in the operator workspace were preserved and not included in CI or any
  release. The local scoped suite uses the current working tree; a full native
  UI run of that dirty working tree was not performed on the operator desktop.

## Remaining Boundaries

- Actual game font calibration, low-end PC capture cost, Arduino movement latency
  and live GPT decision quality are NOT established by simulations.
- Durable write-ahead/result persistence remains synchronous at action/routine
  boundaries. Slow disks can delay those boundaries; no unverified asynchronous
  save optimization was introduced. Local navigation avoids a model call and a
  session save for every individual movement hold, but does not remove storage
  latency or the possibility of VM/host scheduling delays.
- This is a bounded feedback controller, not uninterrupted infinite key holds or
  a model that reacts to every frame. Firmware is unchanged. Holds still release
  between segments; model/transport latency remains at slice boundaries.
- Stage meaning, correct digit labels, goals, route choice and final reward
  interpretation remain GPT responsibilities. A recorded review cannot force
  correct interpretation; unreported semantic stage changes cannot be detected
  universally. Movement scene guards provide a narrower mechanical check.
- Dynamic arbitrary branch graphs and live-GPT-required exported plans remain
  unrepresentable, explicitly incomplete. This overhaul does not claim universal
  autonomous game learning or perfect reproduction after a single success.
- Existing evidence is retained. New uncited streaming frames do not accumulate
  on disk, but referenced evidence/receipts still grow under the existing quota.
  No destructive legacy-data garbage collection was introduced.
- A new user-started experience continuation loads knowledge, not old input
  queues/window handles. It requires a fresh observation and new experience review.
