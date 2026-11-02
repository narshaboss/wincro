# GPT Play Boundary

- This is an opt-in, bounded live game lab. Recording authoring remains read-only
  and autonomous without questions; only this lab has a real user question flow.
- Never expand recording JOB_TOOLS or enable generic Codex shell/computer tools.
- Inputs must pass through WinCro's configured InputController. No Parsec or
  direct model-driven OS input. No firmware uploads, backend substitution or raw
  key-down/key-up exposed to the model. KQ atomically releases Arduino keys.
- GL1 is the opt-in timed-input protocol. Keep legacy KQ/KP/KR semantics unchanged.
  Its firmware enforces duration and a heartbeat lease without a blocking delay.
  Never upload firmware from GPT play or fall back to software on unsupported devices.
- Live sessions have no default elapsed/action/attempt/normal-turn ceiling. Keep
  recording-analysis limits separate. Storage integrity and individual device leases
  remain enforced; never substitute infinite key holds for a continuous session.
- play_control executes up to eight GL1 segments, at most two seconds combined,
  then returns a fresh image in the same call. It is not autonomous game-state
  understanding. A partial bundle is uncertain, never a safe-to-resend request.
- Archive old frames/receipts durably before trimming hot caches. Archived request
  IDs must still deduplicate; historical images must never regain input freshness.
- Learning, routine reuse, candidate trials and native gpt_play_behavior playback
  must use the same versioned program validator/executor. Never flatten timed/guarded
  behavior to legacy naked key actions. Dynamic GPT-dependent branches stay incomplete.
- Experience is task-scoped and durable. Technical errors are not game defeats.
  Resume uses a fresh selected target and observation, never old HWNDs or input queues.
  Uncertain deliveries require evidence-backed reconciliation, never blind replay.
- Existing plans, recordings, maps and both special-mode engines are protected.
  The player entrypoint decorators are admission gates, not algorithm overrides.
- Candidate compiler owns the accepted schema. Do not accept arbitrary imported
  plans or action dictionaries. Unsupported dynamic behavior must stay incomplete.
- During candidate replay GPT is read-only. An intervention invalidates a trial.
  Device ACK is not game outcome. Evidence/reviews are traceability, not proof.
- Actual gameplay, UI/input tests require an isolated test environment; never
  manipulate the operator's main PC for verification. Keep simulated/native/model
  and real-game test results explicitly separate.
- Publication is app-owned, NEW IDs only, no overwrite or playlist insertion.
  Save JSON/assets, read them through AutomationPlan, compare, then report success.
