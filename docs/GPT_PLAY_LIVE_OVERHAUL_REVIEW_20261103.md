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

Verification results and isolated Windows CI reference will be recorded after
the final source run. Tests use synthetic screens and fake devices locally;
native UI, transport and full regression run only on isolated CI Windows.

## Remaining Boundaries

- Actual game font calibration, low-end PC capture cost, Arduino movement latency
  and live GPT decision quality are NOT established by simulations.
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
