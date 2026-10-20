# WinCro 1.0.304

## Changes

- Add Windows CryptoAPI certificate-chain verification through an explicit, request-local truststore SSL context. Keep hostname and certificate verification enabled; do not globally replace Python SSL behavior or modify the Windows certificate store.
- Retain independent system/OpenSSL and bundled certifi CA fallbacks. A broken system-context initialization no longer prevents the bundled CA fallback. Log initialization failures instead of silently hiding them.
- Declare and pin certifi and truststore dependencies, explicitly bundle the CA data and native backend, and block releases whose frozen TLS modules or CA data are missing.
- Preserve HTTP errors such as GitHub rate limits instead of retrying and misreporting them as certificate failures. Improve certificate-failure diagnostics.
- No plan, map, playback, input, or special-mode algorithm changes. Versions are synchronized to 1.0.304 / 1.0.304.0.

## Verification And Limits

Regression tests run on GitHub, not the user's main PC. Tests cover context initialization failures, secure fallbacks, HTTP error handling, and real TLS handshakes against a temporary local HTTPS server (valid, untrusted, wrong-hostname, expired, and incomplete-chain certificates). Frozen modules, CA data, versions, and release checksums are checked before publication.

The reported PC's exact trust-store/network condition has not been inspected. Version 1.0.303 already contains certifi, so absence of that file is not established as the cause. Incorrect clocks, an untrusted HTTPS-inspection proxy, or damaged Windows trust settings can still require PC-level repair; certificate verification is never disabled.

A PC whose existing updater cannot establish HTTPS may need a one-time browser/manual update before this fix can take effect. Publishing a new version does not prove installation on every PC. The six pre-existing expected-failure tests remain separate limitations.

Technical references: [Python SSL](https://docs.python.org/3.11/library/ssl.html), [Truststore](https://truststore.readthedocs.io/en/stable/).
