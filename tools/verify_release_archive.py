"""Verify a frozen release against a Git source archive without executing WinCro."""

import argparse
import ast
import hashlib
import json
import ssl
import sys
import types
import zipfile
from pathlib import Path, PurePosixPath


def fingerprint(code):
    return (
        code.co_code, code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars,
        code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags,
        tuple(fingerprint(item) if isinstance(item, types.CodeType) else item for item in code.co_consts),
    )


def verify(args):
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Use Python 3.11 to inspect this release's bytecode")
    if args.site_packages:
        sys.path.append(str(args.site_packages))
    from PyInstaller.archive.readers import CArchiveReader

    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    assert args.checksum.read_text(encoding="utf-8-sig").split() == [digest, args.archive.name]
    with zipfile.ZipFile(args.source) as source, zipfile.ZipFile(args.archive) as bundle:
        assert bundle.testzip() is None, "ZIP CRC mismatch"
        members = {name.replace("\\", "/") for name in bundle.namelist() if not name.endswith("/")}
        assert all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                   and ":" not in name for name in members), "Unsafe ZIP member"
        tree = ast.parse(source.read("WinCro.spec").decode("utf-8-sig"))
        collector = next(node for node in tree.body
                         if isinstance(node, ast.FunctionDef) and node.name == "collect_packaged_data")
        exclusions = {node.targets[0].id: ast.literal_eval(node.value) for node in collector.body
                      if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                      and node.targets[0].id.startswith("excluded_")}
        expected = set()
        for name in source.namelist():
            if not name.startswith("data/") or name.endswith("/"):
                continue
            path = PurePosixPath(name)
            lower = path.name.casefold()
            if (path.parts[1] in exclusions["excluded_top_level_dirs"] or "__pycache__" in path.parts
                    or path.name in exclusions["excluded_names"] or ".bak" in lower or "backup" in lower
                    or lower.endswith(".old") or path.name.endswith(exclusions["excluded_suffixes"])
                    or path.match("data/digit_templates/debug_region_*.png")):
                continue
            expected.add(name)
        assert {name for name in members if name.startswith("_internal/data/")} == {
            "_internal/" + name for name in expected}, "Packaged data set mismatch"
        plans = []
        for name in sorted(expected):
            actual, original = bundle.read("_internal/" + name), source.read(name)
            if name.endswith(".json"):
                assert json.loads(actual.decode("utf-8-sig")) == json.loads(original.decode("utf-8-sig")), name
            else:
                assert actual == original, name
            if name.startswith("data/plans/") and name.endswith(".json"):
                plans.append(name)
                if args.plans_backup:
                    backup = (args.plans_backup / PurePosixPath(name).name).read_text(encoding="utf-8-sig")
                    assert json.loads(actual.decode("utf-8-sig")) == json.loads(backup), name
        for name in ("icon.ico", "icon_preview.png"):
            assert bundle.read("_internal/" + name) == source.read(name)
        executables = [name for name in members if name.endswith(".exe") and "/" not in name]
        assert len(executables) == 1
        args.extract.mkdir(parents=True, exist_ok=True)
        executable = args.extract / executables[0]
        executable.write_bytes(bundle.read(executables[0]))
        ca_context = ssl.create_default_context(cadata=bundle.read("_internal/certifi/cacert.pem").decode("ascii"))
        ca_count = ca_context.cert_store_stats()["x509_ca"]
        assert ca_count > 50
        pyz = CArchiveReader(str(executable)).open_embedded_archive("PYZ.pyz")
        for name in ("certifi", "truststore", "truststore._windows", "src.utils.notification_delivery",
                     "src.utils.window_control", "src.player.window_control_runtime"):
            assert name in pyz.toc, name
        verified_modules = []
        for module in sorted(pyz.toc):
            if module != "src" and not module.startswith("src."):
                continue
            path = module.replace(".", "/")
            file = path + ".py" if path + ".py" in source.namelist() else path + "/__init__.py"
            code = compile(source.read(file).decode("utf-8-sig"), "reviewed", "exec", dont_inherit=True)
            assert fingerprint(pyz.extract(module)) == fingerprint(code), f"Frozen module mismatch: {module}"
            verified_modules.append(module)
        assert args.version in pyz.extract("src").co_consts
        assert args.version in pyz.extract("src.utils.config").co_consts
    return {
        "verified": True, "version": args.version, "sha256": digest,
        "archive_bytes": args.archive.stat().st_size, "data_files": len(expected),
        "plans": len(plans), "modules": len(verified_modules), "ca_certificates": ca_count,
        "executable": str(executable), "application_executed": False, "input_used": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    for name in ("archive", "checksum", "source", "extract"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plans-backup", type=Path)
    parser.add_argument("--site-packages", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args)
    payload = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
