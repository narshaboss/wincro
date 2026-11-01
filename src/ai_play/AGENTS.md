# GPT Play Boundary

- This is an opt-in, bounded live game lab. Recording authoring remains read-only
  and autonomous without questions; only this lab has a real user question flow.
- Never expand recording JOB_TOOLS or enable generic Codex shell/computer tools.
- Inputs must pass through WinCro's configured InputController. No Parsec or
  direct model-driven OS input. No firmware uploads, backend substitution or raw
  key-down/key-up exposed to the model. KQ atomically releases Arduino keys.
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
