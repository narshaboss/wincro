# WinCro 1.0.303

## Changes

- Fix duplicate auto-hunt action IDs that made image actions 4-2 and 4-3 appear as keyboard action 3-2. Give four duplicate actions stable unique IDs without changing action order or other action settings.
- Conservatively repair unambiguous legacy IDs when loading a plan and reject invalid IDs before saving. Ambiguous ID-only links and special-mode/map ownership are never guessed or silently reassigned.
- Restore only action 4-2's recognition threshold to the version 1.0.300 value, 80.82%. Versions 1.0.301 and 1.0.302 shipped 90.57%. The two target images, search region, color/brightness verification, and recognition algorithms are unchanged.
- Fix first Discord alerts being suppressed by the cooldown shortly after Windows startup. Keep the existing cooldown for repeated alerts. Verify PC-number-first formatting and the corrected action number through the notification path.
- Synchronize application/package version 1.0.303 and Windows file/product version 1.0.303.0.

## Verification

- Full regression suite, explicit special-mode isolation, durable plan-save/reload tests, and mock Discord delivery tests.
- Real OpenCV matching against a deterministic synthetic frame reproduces the old 90.57% rejection and restored 80.82% acceptance of an 88.79% match. Blank frames and matches outside the configured region are rejected.
- Verify the downloaded draft ZIP's checksum, packaged plans/assets, executable resources, and embedded runtime code before publication.
- No live Discord message, Parsec connection, or operational playback interruption is part of release verification.

## Remaining Limits

The six existing expected-failure tests remain unresolved. Special-mode heartbeat blind spots, monitoring-wait notification exclusions, and the existing cooldown after failed delivery remain separate limitations. Synthetic matching and mocked delivery do not prove all real-world screens or phone notification previews.

Published availability is not confirmation of installation on every PC. Existing startup update checks remain unchanged. No special-mode engine or map data is changed in this release.
