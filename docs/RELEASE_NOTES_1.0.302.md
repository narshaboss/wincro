# WinCro 1.0.302

## Changes

- Include the user's latest auto-hunt plan changes: two action `enabled` settings changed from true to false. Preserve action order, all other plan settings, and all other plans.
- Synchronize the application version, package version, executable file/product versions, and release tag at 1.0.302 (Windows resource version 1.0.302.0).
- No playback engine, special-mode algorithm, map, recovery behavior, or update mechanism changes.

## Release Checks

- Run the full regression suite and explicit special-mode isolation and packaging/version checks before publication.
- Verify the downloaded archive's checksum, packaged plans/assets, executable metadata, and isolated launch before publishing the draft.
- Verify the public latest-release endpoint and the real update checker after publication.

## Limitations

The six existing expected-failure tests remain unresolved. They document sequence save handling, damaged configuration preservation, rejected settings mutating live state, synchronous input release on stop, non-interruptible waits, and replaced-image cache refresh. This plan-only update does not claim to fix those issues.

Publishing the release does not confirm installation on every PC. Clients check for updates at startup; verify each installed version separately. Existing packaged-plan replacement behavior is unchanged.
