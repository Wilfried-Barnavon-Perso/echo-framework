# -*- coding: utf-8 -*-
"""Tests unitaires Lot 1 : Worker PTY/SSH unifié, certificats OpenSSH, verrou serveur et schéma Broker."""

from __future__ import annotations

import contextvars
import importlib.util
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = REPO_ROOT / "21-docker-coding-worker"
BROKER_DIR = REPO_ROOT / "23-docker-mcp-broker"


def _load_worker_api():
    """Charge 21-docker-coding-worker/worker_api.py dans un module isolé."""
    mod_name = "echo_test_worker_api"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    if str(WORKER_DIR) not in sys.path:
        sys.path.insert(0, str(WORKER_DIR))
    spec = importlib.util.spec_from_file_location(mod_name, WORKER_DIR / "worker_api.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _load_broker_server():
    """Charge 23-docker-mcp-broker/server.py avec stubs légers pour ses dépendances internes."""
    mod_name = "echo_test_broker_server"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    core_pkg = types.ModuleType("core")
    core_sec = types.ModuleType("core.security")
    core_sec.user_id_var = contextvars.ContextVar("user_id", default="system")
    core_sec.chat_id_var = contextvars.ContextVar("chat_id", default="")
    core_sec.credentials_var = contextvars.ContextVar("credentials", default={})

    modules_pkg = types.ModuleType("modules")
    m5_proxy = types.ModuleType("modules.m5_proxy_mcp")
    m5_proxy.proxy_mcp_request = mock.AsyncMock()
    m5_proxy.AuthRequiredError = type("AuthRequiredError", (Exception,), {})
    m5_proxy._parse_and_validate_credentials = lambda raw: ({}, "")

    m5_providers = types.ModuleType("modules.m5_providers")
    m5_providers.get_service_provider = lambda service: None

    m2_jobs = types.ModuleType("modules.m2_jobs_omnisearch")
    m2_jobs.setup_jobs_omnisearch_mcp = lambda mcp: None

    m3_corp = types.ModuleType("modules.m3_corporate")
    m3_corp.register_corporate_tools = lambda mcp: None

    m4_acad = types.ModuleType("modules.m4_academic")
    m4_acad.register_academic_tools = lambda mcp: None

    stubs = {
        "core": core_pkg,
        "core.security": core_sec,
        "modules": modules_pkg,
        "modules.m5_proxy_mcp": m5_proxy,
        "modules.m5_providers": m5_providers,
        "modules.m2_jobs_omnisearch": m2_jobs,
        "modules.m3_corporate": m3_corp,
        "modules.m4_academic": m4_acad,
    }

    root_logger = logging.getLogger()
    saved_handlers = root_logger.handlers[:]
    saved_level = root_logger.level
    try:
        with mock.patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location(mod_name, BROKER_DIR / "server.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        return module
    finally:
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


class _FakeStdin:
    def __init__(self, popen: "_FakePopen"):
        self._popen = popen

    def write(self, data: bytes):
        self._popen.written_inputs.append(data)
        if self._popen.on_input:
            self._popen.on_input(data)

    def flush(self):
        pass


class _FakePopen:
    """Sous-processus factice portable utilisant os.pipe() pour simuler stdout/stdin."""

    def __init__(self, on_input=None, initial_bytes: bytes = b""):
        r_fd, w_fd = os.pipe()
        self._w_fd = w_fd
        self._w_closed = False
        self.stdout = os.fdopen(r_fd, "rb", buffering=0)
        self.written_inputs: list[bytes] = []
        self.on_input = on_input
        self.stdin = _FakeStdin(self)
        self.pid = 999999
        self._rc = None
        if initial_bytes:
            self.emit_output(initial_bytes)

    def emit_output(self, data: bytes):
        if not self._w_closed:
            os.write(self._w_fd, data)

    def poll(self):
        return self._rc

    def terminate(self):
        self.kill()

    def kill(self):
        self._rc = 0
        if not self._w_closed:
            self._w_closed = True
            try:
                os.close(self._w_fd)
            except OSError:
                pass


class TestPrepareSshBwrapAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker_api = _load_worker_api()

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="echo_test_ssh_auth_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

    def test_private_key_only_creates_id_key_and_batch_mode(self):
        raw_key = "-----BEGIN OPENSSH PRIVATE KEY-----\\nAAAA\\nBBBB\\n-----END OPENSSH PRIVATE KEY-----"
        extra_bwrap, ssh_args = self.worker_api._prepare_ssh_bwrap_auth(
            ssh_tmp_dir=self.tmp_dir,
            host="192.168.1.50",
            port=2222,
            user="ubuntu",
            private_key=raw_key,
            interactive=False,
            command="uname -a",
        )
        key_path = os.path.join(self.tmp_dir, "id_key")
        self.assertTrue(os.path.isfile(key_path))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(key_path).st_mode), 0o600)
        with open(key_path, "r", encoding="utf-8") as kf:
            content = kf.read()
        self.assertTrue(content.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n"))
        self.assertTrue(content.endswith("-----END OPENSSH PRIVATE KEY-----\n"))

        self.assertEqual(extra_bwrap, ["--ro-bind-try", self.tmp_dir, "/ssh_auth"])
        self.assertEqual(ssh_args[0], "ssh")
        self.assertIn("-i", ssh_args)
        self.assertIn("/ssh_auth/id_key", ssh_args)
        self.assertIn("IdentitiesOnly=yes", ssh_args)
        self.assertIn("BatchMode=yes", ssh_args)
        self.assertNotIn("-tt", ssh_args)
        self.assertEqual(ssh_args[-2:], ["ubuntu@192.168.1.50", "uname -a"])

    def test_private_key_with_certificate(self):
        extra_bwrap, ssh_args = self.worker_api._prepare_ssh_bwrap_auth(
            ssh_tmp_dir=self.tmp_dir,
            host="srv.example.org",
            port=22,
            user="deploy",
            private_key="-----BEGIN PRIVATE KEY-----\r\nMIIE\r\n-----END PRIVATE KEY-----",
            certificate="ssh-ed25519-cert-v01@openssh.com AAAAcertpayload\\n",
            interactive=False,
            command="whoami",
        )
        cert_path = os.path.join(self.tmp_dir, "id_key-cert.pub")
        self.assertTrue(os.path.isfile(cert_path))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(cert_path).st_mode), 0o600)
        with open(cert_path, "r", encoding="utf-8") as cf:
            cert_content = cf.read()
        self.assertEqual(cert_content, "ssh-ed25519-cert-v01@openssh.com AAAAcertpayload\n")

        self.assertIn("--ro-bind-try", extra_bwrap)
        self.assertIn("CertificateFile=/ssh_auth/id_key-cert.pub", ssh_args)
        self.assertIn("BatchMode=yes", ssh_args)

    def test_private_key_with_passphrase_uses_sshpass_prompt(self):
        extra_bwrap, ssh_args = self.worker_api._prepare_ssh_bwrap_auth(
            ssh_tmp_dir=self.tmp_dir,
            host="10.0.0.2",
            port=22,
            user="admin",
            password="secret-passphrase",
            private_key="-----BEGIN PRIVATE KEY-----\nABCD\n-----END PRIVATE KEY-----",
            interactive=False,
            command="id",
        )
        self.assertIn("--setenv", extra_bwrap)
        self.assertIn("SSHPASS", extra_bwrap)
        self.assertIn("secret-passphrase", extra_bwrap)
        self.assertEqual(ssh_args[:5], ["sshpass", "-e", "-P", "assphrase", "ssh"])
        self.assertNotIn("BatchMode=yes", ssh_args)

    def test_password_only_uses_sshpass_and_disables_pubkey(self):
        extra_bwrap, ssh_args = self.worker_api._prepare_ssh_bwrap_auth(
            ssh_tmp_dir=self.tmp_dir,
            host="10.0.0.3",
            port=22,
            user="ops",
            password="pw-only",
            interactive=False,
            command="ls -la",
        )
        self.assertEqual(extra_bwrap, ["--setenv", "SSHPASS", "pw-only"])
        self.assertEqual(ssh_args[:3], ["sshpass", "-e", "ssh"])
        self.assertIn("PreferredAuthentications=password,keyboard-interactive", ssh_args)
        self.assertIn("PubkeyAuthentication=no", ssh_args)
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "id_key")))

    def test_interactive_mode_uses_tt_without_command(self):
        _, ssh_args = self.worker_api._prepare_ssh_bwrap_auth(
            ssh_tmp_dir=self.tmp_dir,
            host="192.168.10.20",
            port=22,
            user="ubuntu",
            password="pw",
            interactive=True,
        )
        self.assertIn("-tt", ssh_args)
        self.assertIn("ServerAliveCountMax=3", ssh_args)
        self.assertEqual(ssh_args[-1], "ubuntu@192.168.10.20")

    def test_validation_rejections(self):
        # Certificat sans clé privée
        with self.assertRaises(ValueError):
            self.worker_api._prepare_ssh_bwrap_auth(
                ssh_tmp_dir=self.tmp_dir,
                host="10.0.0.1",
                port=22,
                user="ubuntu",
                password="pw",
                certificate="ssh-ed25519-cert-v01@openssh.com AAAA",
                interactive=True,
            )

        # Host ou user commençant par '-'
        with self.assertRaises(ValueError):
            self.worker_api._prepare_ssh_bwrap_auth(
                ssh_tmp_dir=self.tmp_dir,
                host="-oProxyCommand=id",
                port=22,
                user="ubuntu",
                password="pw",
                interactive=True,
            )
        with self.assertRaises(ValueError):
            self.worker_api._prepare_ssh_bwrap_auth(
                ssh_tmp_dir=self.tmp_dir,
                host="10.0.0.1",
                port=22,
                user="-root",
                password="pw",
                interactive=True,
            )

        # Port hors plage ou invalide
        for bad_port in (0, 65536, "notaport"):
            with self.assertRaises(ValueError):
                self.worker_api._prepare_ssh_bwrap_auth(
                    ssh_tmp_dir=self.tmp_dir,
                    host="10.0.0.1",
                    port=bad_port,
                    user="ubuntu",
                    password="pw",
                    interactive=True,
                )

        # Absence totale d'auth
        with self.assertRaises(ValueError):
            self.worker_api._prepare_ssh_bwrap_auth(
                ssh_tmp_dir=self.tmp_dir,
                host="10.0.0.1",
                port=22,
                user="ubuntu",
                interactive=True,
            )


class TestPostSshEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker_api = _load_worker_api()

    def setUp(self):
        self.sandbox_root = tempfile.mkdtemp(prefix="echo_test_ssh_route_")
        self.addCleanup(lambda: shutil.rmtree(self.sandbox_root, ignore_errors=True))
        self.client = self.worker_api.app.test_client()

    def test_post_ssh_with_certificate_mounts_cert_and_runs_bwrap(self):
        fake_proc = mock.MagicMock(returncode=0, stdout="uid=1000(ubuntu)\n", stderr="")
        with mock.patch.object(self.worker_api, "_get_sandbox_dir", return_value=self.sandbox_root), \
             mock.patch.object(self.worker_api.subprocess, "run", return_value=fake_proc) as run_mock:
            resp = self.client.post("/ssh", json={
                "host": "10.0.0.5",
                "port": 22,
                "user": "ubuntu",
                "command": "id",
                "private_key": "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----",
                "certificate": "ssh-ed25519-cert-v01@openssh.com AAAACERT",
            })

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn("ubuntu", payload["output"])

        cmd = run_mock.call_args[0][0]
        self.assertEqual(cmd[0], "bwrap")
        self.assertIn("CertificateFile=/ssh_auth/id_key-cert.pub", cmd)
        self.assertIn("/ssh_auth/id_key", cmd)
        self.assertEqual(cmd[-2:], ["ubuntu@10.0.0.5", "id"])

    def test_post_ssh_rejects_certificate_without_private_key(self):
        resp = self.client.post("/ssh", json={
            "host": "10.0.0.5",
            "port": 22,
            "user": "ubuntu",
            "command": "id",
            "password": "pw",
            "certificate": "ssh-ed25519-cert-v01@openssh.com AAAACERT",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["status"], "error")


class TestUnifiedTerminalsLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker_api = _load_worker_api()

    def setUp(self):
        self.sandbox_root = tempfile.mkdtemp(prefix="echo_test_term_")
        self.addCleanup(lambda: shutil.rmtree(self.sandbox_root, ignore_errors=True))
        self.client = self.worker_api.app.test_client()
        self._created_ids: list[str] = []

    def tearDown(self):
        for sid in list(self._created_ids):
            self.worker_api.TERMINAL_MANAGER.terminate_session(sid)
        self._created_ids.clear()

    def test_local_session_autonaming_ansi_strip_and_input(self):
        popens: list[_FakePopen] = []

        def fake_spawn(session_self, cmd, use_pty, slave_fd=None, env=None):
            def on_in(data: bytes):
                # Simule une sortie avec séquences ANSI, OSC et retours chariot
                popen.emit_output(
                    b"\x1b]0;bash\x07\x1b[32mhello\x1b[0m\r\n10%\r50%\r100%\r\n"
                )

            popen = _FakePopen(on_input=on_in, initial_bytes=b"\x1b[36msandbox:~$ \x1b[0m")
            popens.append(popen)
            return popen

        with mock.patch.object(self.worker_api, "_get_sandbox_dir", return_value=self.sandbox_root), \
             mock.patch.object(self.worker_api.TerminalSession, "_spawn_session_process", new=fake_spawn):
            r1 = self.client.post("/api/terminals", json={"user_id": "u1", "chat_id": "c1"})
            self.assertEqual(r1.status_code, 200)
            s1 = r1.get_json()
            self._created_ids.append(s1["id"])
            self.assertEqual(s1["mode"], "local")
            self.assertEqual(s1["account_id"], "")
            self.assertEqual(s1["target"], "local:/sandbox")
            self.assertEqual(s1["name"], "Sandbox")
            self.assertFalse(s1["agent_locked"])

            r2 = self.client.post("/api/terminals", json={"user_id": "u1", "chat_id": "c1"})
            self.assertEqual(r2.status_code, 200)
            s2 = r2.get_json()
            self._created_ids.append(s2["id"])
            self.assertEqual(s2["name"], "Sandbox #2")

            # Listing filtré
            lst = self.client.get("/api/terminals?user_id=u1&chat_id=c1")
            self.assertEqual(lst.status_code, 200)
            self.assertEqual(lst.get_json()["count"], 2)

            # Envoi d'une commande par l'agent (déverrouillé par défaut en local)
            inp = self.client.post(f"/api/terminals/{s1['id']}/input", json={
                "actor": "agent",
                "input": "echo hello",
                "wait_ms": 100,
            })
            self.assertEqual(inp.status_code, 200)
            inp_json = inp.get_json()
            self.assertEqual(inp_json["status"], "success")
            self.assertIn("hello", inp_json["text"])
            self.assertIn("100%", inp_json["text"])
            self.assertNotIn("10%\r", inp_json["text"])
            self.assertNotIn("\x1b", inp_json["text"])

            # Lecture buffer complet et contrôle de nettoyage ANSI/CR
            buf = self.client.get(f"/api/terminals/{s1['id']}/buffer?tail_lines=50")
            self.assertEqual(buf.status_code, 200)
            buf_json = buf.get_json()
            self.assertIn("hello", buf_json["text"])
            self.assertNotIn("\x1b", buf_json["text"])

    def test_ssh_session_locked_by_default_enforces_423_and_cleans_tmp_dir(self):
        def fake_spawn(session_self, cmd, use_pty, slave_fd=None, env=None):
            def on_in(data: bytes):
                popen.emit_output(b"root@vps:~# ok\n")

            popen = _FakePopen(on_input=on_in, initial_bytes=b"Welcome to Ubuntu\r\n")
            return popen

        ssh_payload = {
            "user_id": "u2",
            "chat_id": "c2",
            "mode": "ssh",
            "account_id": "vps-prod",
            "agent_locked": False,  # Doit être forcé à True à l'ouverture SSH
            "ssh_config": {
                "host": "192.168.1.50",
                "port": 22,
                "user": "ubuntu",
                "private_key": "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----",
                "certificate": "ssh-ed25519-cert-v01@openssh.com AAAACERT",
            },
        }

        with mock.patch.object(self.worker_api, "_get_sandbox_dir", return_value=self.sandbox_root), \
             mock.patch.object(self.worker_api.TerminalSession, "_spawn_session_process", new=fake_spawn):
            r = self.client.post("/api/terminals", json=ssh_payload)
            self.assertEqual(r.status_code, 200)
            sess_json = r.get_json()
            sid = sess_json["id"]
            self._created_ids.append(sid)

            self.assertEqual(sess_json["mode"], "ssh")
            self.assertEqual(sess_json["name"], "vps-prod")
            self.assertEqual(sess_json["account_id"], "vps-prod")
            self.assertEqual(sess_json["target"], "ubuntu@192.168.1.50:22")
            self.assertTrue(sess_json["agent_locked"])

            sess_obj = self.worker_api.TERMINAL_MANAGER.get_session(sid)
            assert sess_obj is not None
            ssh_tmp_dir = sess_obj._ssh_tmp_dir
            self.assertIsNotNone(ssh_tmp_dir)
            self.assertTrue(os.path.isdir(ssh_tmp_dir))
            self.assertTrue(os.path.isfile(os.path.join(ssh_tmp_dir, "id_key-cert.pub")))

            # Lecture buffer autorisée même sous verrou
            time.sleep(0.05)
            buf = self.client.get(f"/api/terminals/{sid}/buffer")
            self.assertEqual(buf.status_code, 200)
            self.assertIn("Welcome to Ubuntu", buf.get_json()["text"])

            # Écriture agent refusée en 423 terminal_locked
            inp_blocked = self.client.post(f"/api/terminals/{sid}/input", json={
                "actor": "agent",
                "input": "ls",
            })
            self.assertEqual(inp_blocked.status_code, 423)
            self.assertEqual(inp_blocked.get_json()["error_code"], "terminal_locked")

            # Suppression agent refusée en 423 terminal_locked
            del_blocked = self.client.delete(f"/api/terminals/{sid}?actor=agent")
            self.assertEqual(del_blocked.status_code, 423)
            self.assertEqual(del_blocked.get_json()["error_code"], "terminal_locked")

            # Déverrouillage explicite via PATCH /lock
            unlock = self.client.patch(f"/api/terminals/{sid}/lock", json={"agent_locked": False})
            self.assertEqual(unlock.status_code, 200)
            self.assertFalse(unlock.get_json()["agent_locked"])

            # Écriture agent autorisée après déverrouillage
            inp_ok = self.client.post(f"/api/terminals/{sid}/input", json={
                "actor": "agent",
                "input": "id",
                "wait_ms": 100,
            })
            self.assertEqual(inp_ok.status_code, 200)
            self.assertIn("ok", inp_ok.get_json()["text"])

            # Reverrouillage puis fermeture autorisée pour actor=user
            relock = self.client.patch(f"/api/terminals/{sid}/lock", json={"agent_locked": True})
            self.assertEqual(relock.status_code, 200)
            self.assertTrue(relock.get_json()["agent_locked"])

            del_user = self.client.delete(f"/api/terminals/{sid}?actor=user")
            self.assertEqual(del_user.status_code, 200)
            self.assertEqual(del_user.get_json()["status"], "terminated")
            self.assertFalse(os.path.exists(ssh_tmp_dir))

    def test_idle_ttl_pruning_terminates_expired_ssh_session(self):
        def fake_spawn(session_self, cmd, use_pty, slave_fd=None, env=None):
            return _FakePopen()

        with mock.patch.object(self.worker_api, "_get_sandbox_dir", return_value=self.sandbox_root), \
             mock.patch.object(self.worker_api.TerminalSession, "_spawn_session_process", new=fake_spawn):
            r = self.client.post("/api/terminals", json={
                "user_id": "u3",
                "chat_id": "c3",
                "mode": "ssh",
                "account_id": "ttl-box",
                "ssh_config": {
                    "host": "10.0.0.9",
                    "port": 22,
                    "user": "ubuntu",
                    "password": "pw",
                },
            })
            self.assertEqual(r.status_code, 200)
            sid = r.get_json()["id"]
            self._created_ids.append(sid)
            sess_obj = self.worker_api.TERMINAL_MANAGER.get_session(sid)
            assert sess_obj is not None
            tmp_dir = sess_obj._ssh_tmp_dir
            assert tmp_dir is not None

            sess_obj.last_activity_at = time.time() - (self.worker_api.TERMINAL_SSH_IDLE_TTL_SEC + 10.0)
            remaining = self.worker_api.TERMINAL_MANAGER.list_sessions(user_id="u3", chat_id="c3")
            self.assertEqual(remaining, [])
            self.assertIsNone(self.worker_api.TERMINAL_MANAGER.get_session(sid))
            self.assertFalse(os.path.exists(tmp_dir))


class TestBrokerSshSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.broker_server = _load_broker_server()

    def test_ssh_schema_exposes_certificate_and_textarea_secret(self):
        ssh_schema = self.broker_server.SERVICE_SCHEMAS["ssh"]
        instructions = ssh_schema["user_required_instructions"]
        self.assertIn("manage_terminal", instructions)
        self.assertNotIn("ssh_executor", json.dumps(ssh_schema))

        fields = ssh_schema["fields"]
        field_ids = [f["id"] for f in fields]
        self.assertIn("private_key", field_ids)
        self.assertIn("certificate", field_ids)

        pk_idx = field_ids.index("private_key")
        cert_idx = field_ids.index("certificate")
        self.assertEqual(cert_idx, pk_idx + 1)
        self.assertEqual(fields[pk_idx]["type"], "textarea_secret")
        self.assertEqual(fields[cert_idx]["type"], "textarea")


if __name__ == "__main__":
    unittest.main()
