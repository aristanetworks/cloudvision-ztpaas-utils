#!/usr/bin/env python3
# Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the COPYING file.
# FOR INTERNAL USE ONLY. NOT FOR DISTRIBUTION.

"""Exercise request-scoped CA filtering without changing system trust."""

import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import os
from pathlib import Path
import select
import socket
from socketserver import StreamRequestHandler, ThreadingTCPServer
import ssl
import subprocess
import sys
import threading
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import requests


SCRIPT = Path(__file__).resolve().parents[1] / "BootstrapScriptWithToken/bootstrap.py"


def load_bootstrap():
    """Load the script without EOS packages or executing its main entrypoint."""
    spec = importlib.util.spec_from_file_location("bootstrap_ca_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sysdb = types.ModuleType("SysdbHelperUtils")
    sysdb.SysdbPathHelper = object
    with patch.dict(sys.modules, {
        "Cell": types.ModuleType("Cell"),
        "requests": Mock(exceptions=requests.exceptions),
        "SysdbHelperUtils": sysdb,
    }):
        spec.loader.exec_module(module)
    return module


def fingerprint(certificate):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()


def openssl(*arguments):
    return subprocess.check_output(["/usr/bin/openssl"] + [str(arg) for arg in arguments],
                                   stderr=subprocess.STDOUT)


@contextlib.contextmanager
def running_server(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@unittest.skipUnless(os.path.isfile("/usr/bin/openssl"), "OpenSSL is required")
class CaTrustTest(unittest.TestCase):
    """Filtering writes only private files; the source trust bundle stays unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        directory = Path(cls.fixtures.name)
        (directory / "root.cnf").write_text(
            "[req]\ndistinguished_name=dn\n[dn]\n[v3_ca]\n"
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always,issuer\n")
        (directory / "leaf.ext").write_text(
            "subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth,clientAuth\n"
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
        for name in ("affected", "unrelated"):
            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=" + name, "-config", directory / "root.cnf",
                    "-extensions", "v3_ca", "-keyout", directory / (name + ".key"),
                    "-out", directory / (name + ".pem"))
            openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1",
                    "-keyout", directory / (name + "-leaf.key"),
                    "-out", directory / (name + "-leaf.csr"))
            openssl("x509", "-req", "-in", directory / (name + "-leaf.csr"),
                    "-CA", directory / (name + ".pem"), "-CAkey", directory / (name + ".key"),
                    "-set_serial", "3", "-days", "1", "-extfile", directory / "leaf.ext",
                    "-out", directory / (name + "-leaf.pem"))
        # A second issuer signs the same public key, changing the certificate fingerprint.
        openssl("req", "-new", "-key", directory / "affected.key", "-subj", "/CN=affected",
                "-out", directory / "cross.csr")
        openssl("x509", "-req", "-in", directory / "cross.csr", "-CA", directory / "unrelated.pem",
                "-CAkey", directory / "unrelated.key", "-set_serial", "2", "-days", "1",
                "-out", directory / "cross.pem")
        cls.affected = (directory / "affected.pem").read_text()
        cls.unrelated = (directory / "unrelated.pem").read_text()
        cls.cross_signed = (directory / "cross.pem").read_text()

    def setUp(self):
        self.bootstrap = load_bootstrap()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.bundle = self.directory / "ca-bundle.crt"
        self.bundle.write_text(self.affected + self.unrelated)
        self.bootstrap.requests.certs.where.return_value = str(self.bundle)
        self.bootstrap.DISTRUSTED_ROOT_FINGERPRINTS = {fingerprint(self.affected)}
        self.bootstrap.DISTRUSTED_SPKI_HASHES = set()
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("REQUESTS_CA_BUNDLE", None)
        os.environ.pop("CURL_CA_BUNDLE", None)
        self.environment = dict(os.environ)
        self.bootstrap.log = Mock()

    def create_bundle(self):
        result = self.bootstrap.createBootstrapCaBundle()
        self.addCleanup(Path(result).unlink, missing_ok=True)
        return result

    def test_filter_preserves_source_environment_and_unrelated_roots(self):
        filtered = Path(self.create_bundle())
        self.assertEqual(filtered.read_text(), self.unrelated)
        self.assertEqual(filtered.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.bundle.read_text(), self.affected + self.unrelated)
        self.assertEqual(dict(os.environ), self.environment)

    def test_repeated_creation_and_already_absent_roots(self):
        first = self.create_bundle()
        second = self.create_bundle()
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), Path(second).read_bytes())
        self.bundle.write_text(self.unrelated)
        self.assertEqual(Path(self.create_bundle()).read_text(), self.unrelated)

    def test_direct_trust_matches_cross_signed_public_key(self):
        self.bootstrap.DISTRUSTED_ROOT_FINGERPRINTS = set()
        public_key = self.bootstrap.runCertificateCommand(
            ["/usr/bin/openssl", "x509", "-pubkey", "-noout"], self.affected.encode("ascii"))
        spki = self.bootstrap.runCertificateCommand(
            ["/usr/bin/openssl", "pkey", "-pubin", "-outform", "DER"], public_key)
        self.bootstrap.DISTRUSTED_SPKI_HASHES = {hashlib.sha256(spki).hexdigest()}
        self.bundle.write_text(self.affected + self.cross_signed + self.unrelated)
        self.assertEqual(Path(self.create_bundle()).read_text(), self.unrelated)

    def test_environment_ca_override_is_filtered_without_mutation(self):
        self.bundle.write_text(self.affected)
        alternate = self.directory / "alternate.pem"
        alternate.write_text(self.affected + self.unrelated)
        for primary, fallback in ((str(alternate), ""), ("", str(alternate)),
                                  (str(alternate), str(self.bundle))):
            overrides = {"REQUESTS_CA_BUNDLE": primary, "CURL_CA_BUNDLE": fallback}
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                self.assertEqual(Path(self.create_bundle()).read_text(), self.unrelated)
                self.assertEqual({key: os.environ[key] for key in overrides}, overrides)

    def test_setup_errors_are_fatal(self):
        for contents in ("", "-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----",
                         self.affected):
            with self.subTest(contents=contents):
                self.bundle.write_text(contents)
                with self.assertRaises(Exception):
                    self.bootstrap.prepareBootstrapCaBundle()
        self.bundle.unlink()
        with self.assertRaises(OSError):
            self.bootstrap.prepareBootstrapCaBundle()
        self.assertEqual(dict(os.environ), self.environment)
        self.assertIn("aborting ZTP", self.bootstrap.log.call_args.args[0])

    def test_openssl_and_temporary_file_errors_are_fatal(self):
        for target, name in ((self.bootstrap.subprocess, "Popen"),
                             (self.bootstrap.tempfile, "mkstemp")):
            with self.subTest(operation=name), patch.object(target, name, side_effect=OSError(name)):
                with self.assertRaisesRegex(OSError, name):
                    self.bootstrap.prepareBootstrapCaBundle()
            self.assertIn("aborting ZTP", self.bootstrap.log.call_args.args[0])
        self.assertEqual(dict(os.environ), self.environment)

    def test_cloud_run_cleans_bundle_on_success_and_error(self):
        for error in (None, RuntimeError("bootstrap failure"), SystemExit(1)):
            with self.subTest(error=error):
                manager = object.__new__(self.bootstrap.CloudBootstrapManager)
                filtered = self.create_bundle()
                with patch.object(self.bootstrap, "prepareBootstrapCaBundle", return_value=filtered), \
                        patch.object(self.bootstrap.BootstrapManager, "run", side_effect=error) as run:
                    if error is None:
                        manager.run()
                    else:
                        with self.assertRaises(type(error)):
                            manager.run()
                run.assert_called_once()
                self.assertFalse(os.path.exists(filtered))
                self.assertIsNone(manager.requestCaBundle)

    def test_setup_failure_stops_before_bootstrap(self):
        manager = object.__new__(self.bootstrap.CloudBootstrapManager)
        with patch.object(self.bootstrap, "createBootstrapCaBundle",
                          side_effect=RuntimeError("CA filtering failed")), \
                patch.object(self.bootstrap.BootstrapManager, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "CA filtering failed"):
                manager.run()
        run.assert_not_called()

    def test_private_file_cleanup_failure_is_best_effort(self):
        manager = object.__new__(self.bootstrap.CloudBootstrapManager)
        filtered = self.create_bundle()
        with patch.object(self.bootstrap, "prepareBootstrapCaBundle", return_value=filtered), \
                patch.object(self.bootstrap.BootstrapManager, "run"), \
                patch.object(os, "unlink", side_effect=OSError("cleanup denied")):
            manager.run()
        self.assertIsNone(manager.requestCaBundle)
        self.assertIn("Could not remove private", self.bootstrap.log.call_args.args[0])

    def test_on_prem_does_not_prepare_private_trust(self):
        manager = object.__new__(self.bootstrap.OnPremBootstrapManager)
        with contextlib.ExitStack() as stack:
            prepare = stack.enter_context(patch.object(self.bootstrap, "prepareBootstrapCaBundle"))
            for method in ("checkWithRedirector", "getClientCertificates", "getCertificatePaths",
                           "getBootstrapScript", "executeBootstrap"):
                stack.enter_context(patch.object(manager, method))
            manager.run()
        prepare.assert_not_called()

    def test_both_requests_use_private_bundle_and_preserve_client_identity(self):
        manager = object.__new__(self.bootstrap.CloudBootstrapManager)
        manager.requestCaBundle = self.create_bundle()
        manager.bootstrapURL = self.bootstrap.urlparse("https://www.arista.io/ztp/bootstrap")
        manager.redirectorURL = self.bootstrap.urlparse("https://www.arista.io/redirector")
        manager.mibStatus = types.SimpleNamespace(
            systemMacAddr="00:11:22:33:44:55",
            root=types.SimpleNamespace(serialNum="serial", modelName="model", hardwareRev="rev"))
        manager.cellID = "cell"
        manager.pathHelper = Mock()
        manager.pathHelper.getEntity.side_effect = RuntimeError("no TPM")
        manager.certificate, manager.key = "device.crt", "device.key"
        self.bootstrap.requests.post.return_value.json.return_value = [{"value": {
            "clusters": {"values": [{"hosts": {"values": ["www.arista.io"]}}]}}}]
        self.bootstrap.requests.get.return_value.text = "bootstrap instructions"
        with patch.object(self.bootstrap, "BOOT_SCRIPT_PATH", str(self.directory / "download")), \
                patch.object(self.bootstrap, "getKeyValueFromFile", return_value="value"):
            manager.checkWithRedirector()
            manager.getBootstrapScript()
        self.assertEqual(self.bootstrap.requests.post.call_args.kwargs["verify"], manager.requestCaBundle)
        self.assertEqual(self.bootstrap.requests.get.call_args.kwargs["verify"], manager.requestCaBundle)
        self.assertEqual(self.bootstrap.requests.get.call_args.kwargs["cert"], ("device.crt", "device.key"))

    def test_certificate_failure_does_not_retry_with_default_roots(self):
        manager = object.__new__(self.bootstrap.CloudBootstrapManager)
        manager.requestCaBundle = self.create_bundle()
        manager.redirectorURL = self.bootstrap.urlparse("https://www.arista.io/redirector")
        manager.mibStatus = types.SimpleNamespace(root=types.SimpleNamespace(serialNum="serial"))
        self.bootstrap.requests.post.side_effect = requests.exceptions.SSLError("verification failed")
        with self.assertRaisesRegex(Exception, "verification failed"):
            manager.checkWithRedirector()
        self.bootstrap.requests.post.assert_called_once()
        self.assertEqual(self.bootstrap.requests.post.call_args.kwargs["verify"], manager.requestCaBundle)

    @contextlib.contextmanager
    def https_server(self, name, require_client_cert=False):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        fixtures = Path(self.fixtures.name)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(fixtures / (name + "-leaf.pem")),
                                str(fixtures / (name + "-leaf.key")))
        if require_client_cert:
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(cafile=str(fixtures / "unrelated.pem"))
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        with running_server(server):
            yield "https://127.0.0.1:{port}/".format(port=server.server_port)

    @contextlib.contextmanager
    def connect_proxy(self, target):
        target_port = int(target.split(":")[2].strip("/"))

        class Handler(StreamRequestHandler):
            def handle(self):
                if not self.rfile.readline().startswith(b"CONNECT "):
                    return
                while self.rfile.readline().strip():
                    pass
                with socket.create_connection(("127.0.0.1", target_port), timeout=5) as upstream:
                    self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    self.wfile.flush()
                    peers = (self.connection, upstream)
                    while True:
                        ready, _, _ = select.select(peers, [], [], 5)
                        if not ready:
                            return
                        for source in ready:
                            try:
                                data = source.recv(65536)
                                if not data:
                                    return
                                destination = upstream if source is self.connection else self.connection
                                destination.sendall(data)
                            except (ConnectionResetError, BrokenPipeError):
                                # A peer closes the tunnel when certificate verification fails.
                                return

        server = ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        with running_server(server):
            yield "http://127.0.0.1:{port}".format(port=server.server_address[1])

    def https_get(self, url, bundle, proxy="", **kwargs):
        return requests.get(url, verify=str(bundle), timeout=5,
                            proxies={"http": proxy, "https": proxy}, **kwargs)

    def test_requests_rejects_removed_root_without_readding_environment_bundle(self):
        filtered = self.create_bundle()
        with self.https_server("affected") as url:
            self.assertEqual(self.https_get(url, self.bundle).status_code, 200)
            with patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": str(self.bundle)}):
                with self.assertRaises(requests.exceptions.SSLError):
                    self.https_get(url, filtered)

    def test_requests_accepts_allowed_root_and_preserves_mutual_tls(self):
        filtered = self.create_bundle()
        fixtures = Path(self.fixtures.name)
        with self.https_server("unrelated", require_client_cert=True) as url:
            response = self.https_get(
                url, filtered,
                cert=(str(fixtures / "unrelated-leaf.pem"), str(fixtures / "unrelated-leaf.key")))
            self.assertEqual(response.status_code, 200)

    def test_requests_still_checks_hostname(self):
        filtered = self.create_bundle()
        with self.https_server("unrelated") as url:
            with self.assertRaises(requests.exceptions.SSLError):
                self.https_get(url.replace("127.0.0.1", "localhost"), filtered)

    def test_requests_uses_filtered_bundle_through_http_proxy(self):
        filtered = self.create_bundle()
        with self.https_server("affected") as url, self.connect_proxy(url) as proxy:
            self.assertEqual(self.https_get(url, self.bundle, proxy).status_code, 200)
            with self.assertRaises(requests.exceptions.SSLError):
                self.https_get(url, filtered, proxy)


if __name__ == "__main__":
    unittest.main()
