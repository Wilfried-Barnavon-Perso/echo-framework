# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""Tests unitaires Lot 3 : Routeur souverain /echo/v1/terminals et validation Pydantic PanelTab."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi import FastAPI
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "16-docker-echo-webui" / "backend"
LIBS_DIR = REPO_ROOT / "14-owui-libs"
if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))


def _build_sqlalchemy_stubs() -> dict[str, types.ModuleType]:
    """Construit des stubs légers pour sqlalchemy lorsque le paquet n'est pas installé dans le venv local."""
    if importlib.util.find_spec("sqlalchemy") is not None:
        return {}

    sa_mod = types.ModuleType("sqlalchemy")
    sa_mod.JSON = object
    sa_mod.BigInteger = object
    sa_mod.Text = object
    sa_mod.Column = lambda *args, **kwargs: None
    sa_mod.update = lambda *args, **kwargs: None

    sa_exc = types.ModuleType("sqlalchemy.exc")
    sa_exc.IntegrityError = Exception

    sa_ext = types.ModuleType("sqlalchemy.ext")
    sa_asyncio = types.ModuleType("sqlalchemy.ext.asyncio")
    sa_asyncio.AsyncSession = type("AsyncSession", (), {})

    return {
        "sqlalchemy": sa_mod,
        "sqlalchemy.exc": sa_exc,
        "sqlalchemy.ext": sa_ext,
        "sqlalchemy.ext.asyncio": sa_asyncio,
    }


def _load_module_with_stubs(mod_name: str, file_path: Path, stubs: dict[str, types.ModuleType]):
    """Charge un module Python en isolant temporairement ses dépendances stubbées dans sys.modules."""
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    merged_stubs = {**_build_sqlalchemy_stubs(), **stubs}
    saved: dict[str, object] = {}
    missing: list[str] = []
    for name, stub in merged_stubs.items():
        if name in sys.modules:
            saved[name] = sys.modules[name]
        else:
            missing.append(name)
        sys.modules[name] = stub

    try:
        spec = importlib.util.spec_from_file_location(mod_name, file_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name in missing:
            sys.modules.pop(name, None)
        for name, orig in saved.items():
            sys.modules[name] = orig  # type: ignore[assignment]


def _load_chat_views_module():
    """Charge models/chat_views.py avec une classe Base factice autonome."""
    db_stub = types.ModuleType("open_webui.internal.db")
    db_stub.Base = type("Base", (), {})

    owui_pkg = types.ModuleType("open_webui")
    internal_pkg = types.ModuleType("open_webui.internal")

    return _load_module_with_stubs(
        "echo_test_chat_views",
        BACKEND_DIR / "open_webui" / "models" / "chat_views.py",
        {
            "open_webui": owui_pkg,
            "open_webui.internal": internal_pkg,
            "open_webui.internal.db": db_stub,
        },
    )


def _load_terminals_router_module():
    """Charge echo/routers/terminals.py avec stubs légers pour auth, DB et Chats."""
    mod_name = "echo_test_terminals_router"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    async def _default_user():
        return types.SimpleNamespace(id="u_default", role="user")

    async def _default_db():
        yield object()

    class _ChatsStub:
        has_chat_access = mock.AsyncMock(return_value=True)

    owui_pkg = types.ModuleType("open_webui")
    echo_pkg = types.ModuleType("open_webui.echo")
    http_utils_stub = types.ModuleType("open_webui.echo.http_utils")
    http_utils_stub.get_echo_verified_user = _default_user

    runtime_stub = types.ModuleType("open_webui.echo.runtime")
    runtime_stub.ensure_echo_sys_path = lambda: None

    internal_pkg = types.ModuleType("open_webui.internal")
    db_stub = types.ModuleType("open_webui.internal.db")
    db_stub.get_async_session = _default_db

    models_pkg = types.ModuleType("open_webui.models")
    chats_stub = types.ModuleType("open_webui.models.chats")
    chats_stub.Chats = _ChatsStub

    return _load_module_with_stubs(
        mod_name,
        BACKEND_DIR / "open_webui" / "echo" / "routers" / "terminals.py",
        {
            "open_webui": owui_pkg,
            "open_webui.echo": echo_pkg,
            "open_webui.echo.http_utils": http_utils_stub,
            "open_webui.echo.runtime": runtime_stub,
            "open_webui.internal": internal_pkg,
            "open_webui.internal.db": db_stub,
            "open_webui.models": models_pkg,
            "open_webui.models.chats": chats_stub,
        },
    )


class TestPanelTabValidation(unittest.TestCase):
    """Vérifie la validation Pydantic stricte (extra='forbid', strict=True) de PanelTab."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.chat_views = _load_chat_views_module()

    def test_panel_tab_accepts_terminal_ssh_metadata(self) -> None:
        tab = self.chat_views.PanelTab(
            id="tab-term-1",
            kind="terminal",
            label="VPS Prod",
            terminalId="term_123",
            terminalMode="ssh",
            terminalAccountId="vps",
            terminalTarget="root@1.2.3.4:22",
            agentLocked=True,
        )
        self.assertEqual(tab.terminalMode, "ssh")
        self.assertEqual(tab.terminalAccountId, "vps")
        self.assertEqual(tab.terminalTarget, "root@1.2.3.4:22")
        self.assertTrue(tab.agentLocked)

    def test_panel_tab_rejects_invalid_terminal_mode(self) -> None:
        with self.assertRaises(ValidationError):
            self.chat_views.PanelTab(
                id="tab-term-2",
                kind="terminal",
                label="Bad Mode",
                terminalMode="rdp",
            )

    def test_panel_tab_rejects_extra_field_and_non_strict_bool(self) -> None:
        with self.assertRaises(ValidationError):
            self.chat_views.PanelTab(
                id="tab-term-3",
                kind="terminal",
                label="Extra",
                unknownField="nope",
            )

        with self.assertRaises(ValidationError):
            self.chat_views.PanelTab(
                id="tab-term-4",
                kind="terminal",
                label="NonStrict",
                agentLocked="true",
            )


class TestWebuiTerminalsRouter(unittest.IsolatedAsyncioTestCase):
    """Vérifie les 5 endpoints de echo/routers/terminals.py."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.router_mod = _load_terminals_router_module()

    def setUp(self) -> None:
        self.user = types.SimpleNamespace(id="user-1", role="user")
        self.app = FastAPI()
        self.app.include_router(self.router_mod.router, prefix="/echo/v1/terminals")
        self.app.dependency_overrides[self.router_mod.get_echo_verified_user] = lambda: self.user

        async def _fake_db():
            yield object()

        self.app.dependency_overrides[self.router_mod.get_async_session] = _fake_db
        self.router_mod.Chats.has_chat_access = mock.AsyncMock(return_value=True)

    async def test_ssh_targets_no_ghost_db_and_no_secrets(self) -> None:
        import echo_terminals
        from echo_db_schema import DB_KIND_IDENTITY, ensure_db
        from echo_state_manager import EchoStateManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_identity_db = os.path.join(tmp_dir, "identity.db")
            with (
                mock.patch.dict(
                    os.environ,
                    {"ECHO_VAULT_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
                ),
                mock.patch.object(echo_terminals, "get_echo_identity_db_path", return_value=fake_identity_db),
                mock.patch("echo_state_manager.get_echo_identity_db_path", return_value=fake_identity_db),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app),
                    base_url="http://test",
                ) as client:
                    # 1. Absence de BDD -> liste vide, zéro BDD fantôme créée
                    resp_empty = await client.get("/echo/v1/terminals/ssh-targets")
                    self.assertEqual(resp_empty.status_code, 200)
                    self.assertEqual(resp_empty.json(), {"items": [], "count": 0})
                    self.assertFalse(os.path.exists(fake_identity_db))

                    # 2. BDD présente avec secrets -> aucun secret exposé
                    ensure_db(fake_identity_db, DB_KIND_IDENTITY)
                    EchoStateManager(user_id=self.user.id).save_vault_credential(
                        "ssh",
                        "vps-prod",
                        json.dumps({
                            "host": "10.0.0.42",
                            "port": 2222,
                            "user": "deploy",
                            "password": "super-secret-password",
                            "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET",
                            "certificate": "ssh-ed25519-cert-v01@openssh.com CERTDATA",
                            "description": "Production VPS",
                        }),
                    )

                    resp_targets = await client.get("/echo/v1/terminals/ssh-targets")
                    self.assertEqual(resp_targets.status_code, 200)
                    body = resp_targets.json()
                    self.assertEqual(body["count"], 1)
                    item = body["items"][0]
                    self.assertEqual(item["account_id"], "vps-prod")
                    self.assertEqual(item["host"], "10.0.0.42")
                    self.assertEqual(item["port"], 2222)
                    self.assertEqual(item["user"], "deploy")
                    self.assertTrue(item["has_certificate"])
                    self.assertNotIn("password", item)
                    self.assertNotIn("private_key", item)
                    self.assertNotIn("certificate", item)

    async def test_get_chat_terminals_access_control(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
        ) as client:
            resp_local = await client.get("/echo/v1/terminals/local:unsaved")
            self.assertEqual(resp_local.status_code, 400)

            self.router_mod.Chats.has_chat_access = mock.AsyncMock(return_value=False)
            resp_forbidden = await client.get("/echo/v1/terminals/chat-other")
            self.assertEqual(resp_forbidden.status_code, 404)

            self.router_mod.Chats.has_chat_access = mock.AsyncMock(return_value=True)
            with mock.patch.object(
                self.router_mod,
                "worker_list_terminals",
                mock.AsyncMock(return_value={"status": "success", "items": [{"id": "term_1"}], "count": 1}),
            ):
                resp_ok = await client.get("/echo/v1/terminals/chat-1")
                self.assertEqual(resp_ok.status_code, 200)
                self.assertEqual(resp_ok.json(), {"items": [{"id": "term_1"}], "count": 1})

    async def test_open_lock_and_delete_broadcasts(self) -> None:
        fake_wm = types.SimpleNamespace(broadcast_terminal_state=mock.AsyncMock())

        local_session = {
            "id": "term_local",
            "mode": "local",
            "name": "Sandbox",
            "agent_locked": False,
            "alive": True,
        }
        ssh_session = {
            "id": "term_ssh",
            "mode": "ssh",
            "account_id": "vps",
            "name": "vps",
            "agent_locked": True,
            "alive": True,
        }
        unlocked_ssh = {**ssh_session, "agent_locked": False}

        with (
            mock.patch.object(self.router_mod, "get_window_manager", return_value=fake_wm),
            mock.patch.object(
                self.router_mod,
                "worker_open_terminal",
                mock.AsyncMock(side_effect=[
                    {"status": "success", "session": local_session},
                    {"status": "success", "session": ssh_session},
                ]),
            ) as open_mock,
            mock.patch.object(
                self.router_mod,
                "worker_set_terminal_lock",
                mock.AsyncMock(side_effect=[
                    {"status": "success", "session": unlocked_ssh},
                    {"status": "error", "error_code": "not_found", "error": "absent"},
                ]),
            ) as lock_mock,
            mock.patch.object(
                self.router_mod,
                "worker_close_terminal",
                mock.AsyncMock(return_value={"status": "terminated", "id": "term_ssh"}),
            ) as close_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://test",
            ) as client:
                r_local = await client.post("/echo/v1/terminals/chat-1/open", json={"mode": "local"})
                self.assertEqual(r_local.status_code, 200)
                self.assertFalse(r_local.json()["agent_locked"])

                r_ssh = await client.post(
                    "/echo/v1/terminals/chat-1/open",
                    json={"mode": "ssh", "account_id": "vps"},
                )
                self.assertEqual(r_ssh.status_code, 200)
                self.assertTrue(r_ssh.json()["agent_locked"])

                r_lock = await client.patch(
                    "/echo/v1/terminals/chat-1/term_ssh/lock",
                    json={"agent_locked": False},
                )
                self.assertEqual(r_lock.status_code, 200)
                self.assertFalse(r_lock.json()["agent_locked"])

                r_lock_missing = await client.patch(
                    "/echo/v1/terminals/chat-1/term_missing/lock",
                    json={"agent_locked": True},
                )
                self.assertEqual(r_lock_missing.status_code, 404)

                r_del = await client.delete("/echo/v1/terminals/chat-1/term_ssh")
                self.assertEqual(r_del.status_code, 200)
                self.assertEqual(r_del.json(), {"status": "terminated", "id": "term_ssh"})

        self.assertEqual(open_mock.await_count, 2)
        lock_mock.assert_any_await("user-1", "chat-1", "term_ssh", False)
        close_mock.assert_awaited_once_with("user-1", "chat-1", "term_ssh", actor="user")
        self.assertEqual(
            fake_wm.broadcast_terminal_state.await_args_list,
            [
                mock.call("user-1", "chat-1", local_session, "opened"),
                mock.call("user-1", "chat-1", ssh_session, "opened"),
                mock.call("user-1", "chat-1", unlocked_ssh, "lock_changed"),
                mock.call("user-1", "chat-1", {"id": "term_ssh"}, "closed"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
