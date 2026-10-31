# Codex Model Catalog Notice

`src/agent_bridge/codex_models_recording.json` is derived from OpenAI Codex,
`codex-rs/models-manager/models.json`, tag `rust-v0.130.0-alpha.5`:
https://github.com/openai/codex/blob/rust-v0.130.0-alpha.5/codex-rs/models-manager/models.json

Copyright OpenAI. Licensed under the Apache License, Version 2.0.
The license is included in `CODEX_LICENSE.txt`.

WinCro modifications: `apply_patch_tool_type` is null and
`experimental_supported_tools` is empty for every entry. Other model metadata
is unchanged. This snapshot is used only for isolated recording-analysis jobs;
it does not alter the user's global Codex model catalog or settings.
