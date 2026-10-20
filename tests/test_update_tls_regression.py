"""TLS regressions; no desktop interaction or changes to system trust stores."""

import datetime
import http.server
import ssl
import sys
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.utils import update_service


def test_windows_native_context_is_request_local_and_verified(monkeypatch):
    native = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(
        SSLContext=lambda protocol: calls.append(protocol) or native,
    ))
    contexts = update_service._verified_ssl_contexts()
    assert contexts[0] == ("Windows 기본 인증서 검증", native)
    assert calls == [ssl.PROTOCOL_TLS_CLIENT]
    assert native.verify_mode == ssl.CERT_REQUIRED and native.check_hostname
    assert all(ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname for _, ctx in contexts)


@pytest.mark.parametrize("native_error", [ImportError("unavailable"), OSError("native init failed"), RuntimeError("unsupported")])
def test_native_initialization_failure_preserves_system_and_bundle(monkeypatch, native_error):
    def fail(_protocol):
        raise native_error

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(SSLContext=fail))
    assert [name for name, _ in update_service._verified_ssl_contexts()] == ["시스템 인증서", "내장 CA 인증서"]


def test_broken_system_store_does_not_block_bundled_ca(monkeypatch):
    original = ssl.create_default_context
    monkeypatch.setattr(sys, "platform", "linux")

    def create(*args, **kwargs):
        if not kwargs.get("cafile"):
            raise OSError("broken default CA path")
        return original(*args, **kwargs)

    monkeypatch.setattr(ssl, "create_default_context", create)
    contexts = update_service._verified_ssl_contexts()
    assert len(contexts) == 1 and contexts[0][0] == "내장 CA 인증서"
    assert contexts[0][1].cert_store_stats()["x509_ca"] > 50


def test_missing_bundle_preserves_system_context_and_logs(monkeypatch, caplog):
    import certifi

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(certifi, "where", lambda: "missing-ca-bundle.pem")
    contexts = update_service._verified_ssl_contexts()
    assert [name for name, _ in contexts] == ["시스템 인증서"]
    assert "내장 CA 인증서 초기화 실패" in caplog.text


def test_no_usable_ca_fails_closed(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("no CA")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ssl, "create_default_context", fail)
    calls = []
    monkeypatch.setattr(update_service.urllib.request, "urlopen", lambda *a, **k: calls.append(k))
    with pytest.raises(ConnectionError, match="신뢰 CA"):
        update_service.ssl_fallback_connect("https://example.invalid")
    assert not calls


def test_failed_system_connection_uses_verified_bundle(monkeypatch):
    contexts = [ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT) for _ in range(2)]
    monkeypatch.setattr(update_service, "_verified_ssl_contexts", lambda: list(zip(("system", "bundle"), contexts)))
    response = SimpleNamespace(geturl=lambda: "https://example.invalid")
    calls = []

    def open_url(request, *, context, timeout):
        calls.append(context)
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        if len(calls) == 1:
            raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))
        return response

    monkeypatch.setattr(update_service.urllib.request, "urlopen", open_url)
    assert update_service.ssl_fallback_connect("https://example.invalid") is response
    assert calls == contexts


def test_all_certificate_failures_are_diagnosed_without_bypass(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(update_service, "_verified_ssl_contexts", lambda: [("system", context), ("bundle", context)])

    def fail(*args, **kwargs):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))

    monkeypatch.setattr(update_service.urllib.request, "urlopen", fail)
    with pytest.raises(ConnectionError, match="PC 날짜·시간") as error:
        update_service.ssl_fallback_connect("https://example.invalid")
    assert "system:" in str(error.value) and "bundle:" in str(error.value)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.parametrize("status", [403, 404, 429, 504])
def test_http_status_is_preserved_without_tls_retries(monkeypatch, status):
    monkeypatch.setattr(update_service, "_verified_ssl_contexts", lambda: [("first", object()), ("second", object())])
    calls = []
    expected = urllib.error.HTTPError("https://example.invalid", status, "HTTP error", {}, None)

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise expected

    monkeypatch.setattr(update_service.urllib.request, "urlopen", fail)
    with pytest.raises(urllib.error.HTTPError) as error:
        update_service.ssl_fallback_connect("https://example.invalid")
    assert error.value is expected and len(calls) == 1


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    path = tmp_path_factory.mktemp("tls")
    now = datetime.datetime.now(datetime.timezone.utc)
    root_key, intermediate_key, leaf_key = [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(3)]

    def certificate(name, key, issuer, issuer_key, *, ca=False, expired=False):
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (x509.CertificateBuilder().subject_name(subject)
                   .issuer_name(issuer.subject if issuer else subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(days=2))
                   .not_valid_after(now + datetime.timedelta(days=-1 if expired else 2))
                   .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
                   .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                  key_encipherment=not ca, data_encipherment=False, key_agreement=False,
                                  key_cert_sign=ca, crl_sign=ca, encipher_only=None, decipher_only=None), critical=True))
        if not ca:
            builder = (builder.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
                       .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False))
        return builder.sign(issuer_key, hashes.SHA256())

    root = certificate("WinCro isolated test CA", root_key, None, root_key, ca=True)
    intermediate = certificate("WinCro isolated intermediate", intermediate_key, root, root_key, ca=True)
    leaf = certificate("localhost", leaf_key, intermediate, intermediate_key)
    expired = certificate("localhost", leaf_key, intermediate, intermediate_key, expired=True)
    pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM)
    (path / "root.pem").write_bytes(pem(root))
    (path / "chain.pem").write_bytes(pem(leaf) + pem(intermediate))
    (path / "expired.pem").write_bytes(pem(expired) + pem(intermediate))
    (path / "incomplete.pem").write_bytes(pem(leaf))
    (path / "key.pem").write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return path


@pytest.mark.parametrize("backend", ["openssl", "native"])
@pytest.mark.parametrize("scenario", ["valid", "untrusted", "wrong-hostname", "expired", "incomplete"])
def test_real_tls_handshakes_remain_verified(monkeypatch, tls_material, backend, scenario):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"verified TLS")

        def log_message(self, *_args):
            pass

    cert = {"expired": "expired.pem", "incomplete": "incomplete.pem"}.get(scenario, "chain.pem")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(tls_material / cert), str(tls_material / "key.pem"))
    if backend == "native":
        import truststore

        client_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if scenario != "untrusted":
        client_context.load_verify_locations(cafile=str(tls_material / "root.pem"))
    monkeypatch.setattr(update_service, "_verified_ssl_contexts", lambda: [(backend, client_context)])
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    host = "127.0.0.1" if scenario == "wrong-hostname" else "localhost"
    url = f"https://{host}:{server.server_port}/"
    try:
        if scenario == "valid":
            with update_service.ssl_fallback_connect(url, timeout=3) as response:
                assert response.read() == b"verified TLS"
        else:
            with pytest.raises(ConnectionError):
                update_service.ssl_fallback_connect(url, timeout=3)
        assert client_context.verify_mode == ssl.CERT_REQUIRED and client_context.check_hostname
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_release_requires_native_tls_and_ca_bundle_verification():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "WinCro.spec").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    assert "collect_data_files('certifi', includes=['cacert.pem'])" in spec
    assert "collect_submodules('truststore')" in spec
    assert "certifi==" in requirements and "truststore==" in requirements
    assert "python tools/verify_release_tls_bundle.py $appDir.FullName" in workflow
    assert workflow.index("Verify packaged TLS dependencies") < workflow.index("Create or update release")
    assert "inject_into_ssl" not in (root / "src/utils/update_service.py").read_text(encoding="utf-8")
