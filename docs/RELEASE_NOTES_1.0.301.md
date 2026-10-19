# WinCro 1.0.301

## Included Changes

- Put the configured PC number on the first line of Discord notifications, ahead of the alert title.
- Include the previously requested removal of screen transition recovery from editing, execution, and serialization. Legacy documents remain readable. Normal repeats, trigger return actions, monitoring, and existing waiting behavior remain available.
- Include the current auto-hunt plan revision without rearranging actions or changing special-mode engines and maps.
- Gate release builds on regression tests. Build a draft release for artifact verification before publication. Refuse to replace an existing version tag or published release.
- Pin release test tools to verified versions and make glyph/trigger-region regressions independent of the runner's optional OCR installation and physical screen height.

## Validation Before Publication

- Local full suite: 1,005 passed, 6 expected failures documenting pre-existing issues.
- Clean Python 3.11 environment: 1,005 passed, 6 expected failures before adding a second, explicit trigger-region screen-size case. The final CI run also covers that additional case.
- Focused special-mode isolation, Discord, legacy-save compatibility, update security, group handoff, and partial-run suite: 87 passed.
- Synthetic editor smoke test: plan windows at 1080x760 and 980x700, sequence window at 950x700. Save/collapse controls visible; no callback exceptions.
- Plan image reference audit: all nine active plan JSON files have their referenced release assets available and tracked.
- Downloaded archive, checksums, packaged plans, icons, excluded local state, and executable metadata are verified before publishing the draft.

## Known Limitations

The six existing expected failures concern missing-record sequence saves, preservation of unreadable configuration, rejected settings mutating live configuration, synchronous device release on stop, non-interruptible waits, and stale image cache entries. This release does not resolve those issues or claim complete runtime stability.

Remote installation confirmation is distinct from release publication. Existing clients with startup update checks enabled discover a published release when restarted; publication alone does not prove that every PC has installed it. Check each PC's installed version to confirm completion.

No Discord message is sent by the regression tests or artifact verification. A live notification on a deployed PC is a separate check.
