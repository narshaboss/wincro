# WinCro 1.0.305

## Changes

- Package the user's saved auto-hunt plan revision without reordering, deleting, or rewriting its actions. The revision enables skip-on-not-found on one action.
- Include the previously tested Discord delivery fix: confirmed message receipts, bounded asynchronous retries, rate-limit handling, verified TLS fallbacks, separate pending/failure/success cooldown states, and explicit transmission diagnostics.
- Preserve the existing mention and notification policy. No silent-message flag or new mention filtering is added. Message delivery confirmation does not guarantee phone vibration or push notification behavior.
- Verify notification runtime modules and CA dependencies inside the frozen executable before creating a draft release. Add durable disk roundtrip checks for all shipped active plans.
- Synchronize application/package version 1.0.305 and Windows file/product version 1.0.305.0.

## Verification And Limits

Executable regression tests run on GitHub, not the user's main PC. Release verification checks saved plans and referenced assets, the downloaded draft ZIP, checksums, executable version resources, and embedded runtime code. No Parsec operation, live Discord message, or local operational playback test is used.

The six existing expected-failure tests remain unresolved. Special-mode heartbeat and monitoring-wait detection exemptions, notification rearming after a settings change, and dependence on the GUI event loop remain separate detection risks. This release changes delivery, not those detection policies or either special-mode engine.

A timeout after Discord stored a message can cause a retry to duplicate it. Confirmed server receipt does not prove phone push/vibration. A PC unable to download updates because of its existing certificate environment may need a one-time manual update. Public availability is not proof of installation on every PC.
