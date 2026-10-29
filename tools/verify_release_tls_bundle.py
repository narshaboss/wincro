"""Inspect the frozen TLS dependencies without launching or importing WinCro."""

import argparse
import json
import ssl
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def fingerprint(code):
    return (
        code.co_code, code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars,
        tuple(fingerprint(item) if isinstance(item, types.CodeType) else item
              for item in code.co_consts),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("application_dir", type=Path)
    args = parser.parse_args()
    executables = list(args.application_dir.glob("*.exe"))
    assert len(executables) == 1, "Expected exactly one application executable"
    pyz = CArchiveReader(str(executables[0])).open_embedded_archive("PYZ.pyz")
    for name in ("certifi", "certifi.core", "truststore", "truststore._api", "truststore._windows"):
        assert name in pyz.toc, f"Missing frozen TLS dependency: {name}"
    verified_modules = []
    for module in ("src.utils.update_service", "src.utils.discord_notifier",
                   "src.utils.notification_delivery", "src.ui.main_window"):
        source = Path(module.replace(".", "/") + ".py").read_text(encoding="utf-8-sig")
        assert fingerprint(pyz.extract(module)) == fingerprint(compile(source, "reviewed", "exec")), module
        verified_modules.append(module)
    ca_path = args.application_dir / "_internal/certifi/cacert.pem"
    assert ca_path.is_file(), "Missing bundled CA data"
    context = ssl.create_default_context(cafile=str(ca_path))
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    count = context.cert_store_stats()["x509_ca"]
    assert count > 50, f"Empty or unexpectedly small bundled CA store: {count}"
    print(json.dumps({"frozen_tls_verified": True, "bundled_ca_count": count,
                      "verified_notification_modules": verified_modules, "application_executed": False}))


if __name__ == "__main__":
    main()
