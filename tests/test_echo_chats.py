# -*- coding: utf-8 -*-
"""Tests unitaires et d'intégration pour echo_chats et les outils UI conversations."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBS_DIR = REPO_ROOT / "14-owui-libs"
TOOLS_DIR = REPO_ROOT / "12-owui-tools"
if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))

import echo_chats  # noqa: E402
import echo_subagent_hub  # noqa: E402
from echo_subagent_hub import compute_subagent_excluded_tools  # noqa: E402
from echo_tool_guides import EchoToolGuidesManager  # noqa: E402


def _ensure_open_webui_stubs() -> None:
    """Fournit des stubs minimaux pour open_webui.* hors conteneur Docker."""
    try:
        import open_webui.tools.builtin  # type: ignore  # noqa: F401
        import open_webui.utils.chat_id  # type: ignore  # noqa: F401
        return
    except Exception:
        pass

    def _noop_fn(*args, **kwargs):
        return None

    class _TaskItem:
        pass

    class _FolderForm:
        pass

    mod_root = sys.modules.get("open_webui") or types.ModuleType("open_webui")
    mod_root.__path__ = getattr(mod_root, "__path__", [])
    mod_tools = sys.modules.get("open_webui.tools") or types.ModuleType("open_webui.tools")
    mod_tools.__path__ = getattr(mod_tools, "__path__", [])
    mod_builtin = sys.modules.get("open_webui.tools.builtin") or types.ModuleType("open_webui.tools.builtin")
    for fn_name in (
        "search_calendar_events",
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "create_automation",
        "update_automation",
        "toggle_automation",
        "delete_automation",
        "create_tasks",
        "update_task",
        "_get_user_tz",
        "_ns_to_dt",
    ):
        setattr(mod_builtin, fn_name, _noop_fn)
    mod_builtin.TaskItem = _TaskItem

    mod_models = sys.modules.get("open_webui.models") or types.ModuleType("open_webui.models")
    mod_models.__path__ = getattr(mod_models, "__path__", [])
    mod_folders = sys.modules.get("open_webui.models.folders") or types.ModuleType("open_webui.models.folders")
    mod_folders.Folders = getattr(mod_folders, "Folders", object)
    mod_folders.FolderForm = getattr(mod_folders, "FolderForm", _FolderForm)
    mod_tags = sys.modules.get("open_webui.models.tags") or types.ModuleType("open_webui.models.tags")
    mod_tags.Tags = getattr(mod_tags, "Tags", object)
    mod_chats = sys.modules.get("open_webui.models.chats") or types.ModuleType("open_webui.models.chats")
    mod_chats.Chats = getattr(mod_chats, "Chats", object)
    mod_chats.is_internal_chat = getattr(mod_chats, "is_internal_chat", lambda *args, **kwargs: False)
    mod_auto = sys.modules.get("open_webui.models.automations") or types.ModuleType("open_webui.models.automations")
    mod_auto.Automations = getattr(mod_auto, "Automations", object)
    mod_cal = sys.modules.get("open_webui.models.calendar") or types.ModuleType("open_webui.models.calendar")
    mod_cal.CalendarEvents = getattr(mod_cal, "CalendarEvents", object)

    mod_utils = sys.modules.get("open_webui.utils") or types.ModuleType("open_webui.utils")
    mod_utils.__path__ = getattr(mod_utils, "__path__", [])
    mod_chat_id = sys.modules.get("open_webui.utils.chat_id") or types.ModuleType("open_webui.utils.chat_id")
    mod_chat_id.is_saved_chat_id = getattr(mod_chat_id, "is_saved_chat_id", lambda *args, **kwargs: True)

    for k, v in {
        "open_webui": mod_root,
        "open_webui.tools": mod_tools,
        "open_webui.tools.builtin": mod_builtin,
        "open_webui.models": mod_models,
        "open_webui.models.folders": mod_folders,
        "open_webui.models.tags": mod_tags,
        "open_webui.models.chats": mod_chats,
        "open_webui.models.automations": mod_auto,
        "open_webui.models.calendar": mod_cal,
        "open_webui.utils": mod_utils,
        "open_webui.utils.chat_id": mod_chat_id,
    }.items():
        sys.modules.setdefault(k, v)


def _load_tool_module(name: str, filename: str):
    """Charge un module d'outil de 12-owui-tools de manière déterministe."""
    if name in sys.modules:
        return sys.modules[name]
    mod_path = TOOLS_DIR / filename
    spec = importlib.util.spec_from_file_location(name, str(mod_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ensure_open_webui_stubs()
generalist_mod = _load_tool_module("tool_generalist_tools", "generalist_tools.py")


class _Cond:
    def __init__(self, op: str, table: str, name: str, val: Any) -> None:
        self.op = op
        self.table = table
        self.name = name
        self.val = val


class _FakeColumn:
    def __init__(self, table: str, name: str) -> None:
        self.table = table
        self.name = name

    def __eq__(self, other: Any) -> _Cond:  # type: ignore[override]
        return _Cond("eq", self.table, self.name, other)

    def __ne__(self, other: Any) -> _Cond:  # type: ignore[override]
        return _Cond("ne", self.table, self.name, other)

    def __ge__(self, other: Any) -> _Cond:
        return _Cond("ge", self.table, self.name, other)

    def __le__(self, other: Any) -> _Cond:
        return _Cond("le", self.table, self.name, other)

    def in_(self, values: Any) -> _Cond:
        return _Cond("in", self.table, self.name, list(values))

    def is_(self, value: Any) -> _Cond:
        return _Cond("is", self.table, self.name, value)

    def desc(self) -> tuple[str, str]:
        return (self.name, "desc")


class _FakeResult:
    def __init__(self, rows: List[tuple]) -> None:
        self._rows = rows

    def all(self) -> List[tuple]:
        return list(self._rows)


class _FakeSelectStmt:
    def __init__(self, *cols: _FakeColumn) -> None:
        self.cols = list(cols)
        self.conditions: List[_Cond] = []
        self.order_specs: List[Any] = []
        self.is_distinct = False

    def where(self, *conds: _Cond) -> "_FakeSelectStmt":
        self.conditions.extend(conds)
        return self

    def distinct(self) -> "_FakeSelectStmt":
        self.is_distinct = True
        return self

    def order_by(self, *specs: Any) -> "_FakeSelectStmt":
        self.order_specs.extend(specs)
        return self


def _make_fake_model(table_name: str, fields: tuple[str, ...]):
    attrs = {f: _FakeColumn(table_name, f) for f in fields}
    return type(table_name, (), attrs)


_FakeChat = _make_fake_model(
    "Chat",
    (
        "id",
        "user_id",
        "title",
        "folder_id",
        "archived",
        "pinned",
        "meta",
        "current_message_id",
        "created_at",
        "updated_at",
        "last_read_at",
        "timer_at",
    ),
)
_FakeChatMessage = _make_fake_model(
    "ChatMessage",
    ("id", "chat_id", "role", "done", "error"),
)
_FakeAutomationRun = _make_fake_model(
    "AutomationRun",
    ("chat_id", "status"),
)


def _match_cond(row: Dict[str, Any], cond: _Cond) -> bool:
    val = row.get(cond.name)
    if cond.op == "eq":
        return val == cond.val
    if cond.op == "ne":
        return val != cond.val
    if cond.op == "ge":
        return (val or 0) >= cond.val
    if cond.op == "le":
        return (val or 0) <= cond.val
    if cond.op == "in":
        return val in cond.val
    if cond.op == "is":
        return val is cond.val
    raise ValueError(f"Unsupported op: {cond.op}")


def _build_fake_owui_deps(
    *,
    chats: Optional[List[Dict[str, Any]]] = None,
    chat_messages: Optional[List[Dict[str, Any]]] = None,
    automation_runs: Optional[List[Dict[str, Any]]] = None,
    active_task_cids: Optional[set[str]] = None,
    pending_approval_cids: Optional[set[str]] = None,
    folders: Optional[List[SimpleNamespace]] = None,
    search_hits: Optional[List[str]] = None,
    snippets: Optional[Dict[str, str]] = None,
    chat_payloads: Optional[Dict[str, Any]] = None,
    response_streams: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    tables = {
        "Chat": list(chats or []),
        "ChatMessage": list(chat_messages or []),
        "AutomationRun": list(automation_runs or []),
    }
    active_set = set(active_task_cids or set())
    pending_set = set(pending_approval_cids or set())
    folders_list = list(folders or [])
    search_list = list(search_hits or [])
    snippets_map = dict(snippets or {})
    payloads_map = dict(chat_payloads or {})
    streams_map = dict(response_streams or {})

    class _FakeSession:
        async def execute(self, stmt: _FakeSelectStmt) -> _FakeResult:
            table_name = stmt.cols[0].table
            rows = [dict(r) for r in tables.get(table_name, [])]
            for cond in stmt.conditions:
                rows = [r for r in rows if _match_cond(r, cond)]

            for spec in reversed(stmt.order_specs):
                if isinstance(spec, tuple):
                    col_name, direction = spec
                    rows.sort(key=lambda r: (r.get(col_name) is None, r.get(col_name) or 0), reverse=(direction == "desc"))
                elif isinstance(spec, _FakeColumn):
                    rows.sort(key=lambda r: str(r.get(spec.name) or ""))

            projected = [tuple(r.get(col.name) for col in stmt.cols) for r in rows]
            if stmt.is_distinct:
                seen = set()
                deduped = []
                for tup in projected:
                    if tup not in seen:
                        seen.add(tup)
                        deduped.append(tup)
                projected = deduped
            return _FakeResult(projected)

    class _FakeDbCtx:
        async def __aenter__(self) -> _FakeSession:
            return _FakeSession()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _FakeChats:
        @staticmethod
        async def get_chats_by_user_id_and_search_text(
            user_id: str,
            search_text: str,
            include_archived: bool = False,
            skip: int = 0,
            limit: int = 60,
        ):
            return [SimpleNamespace(id=cid) for cid in search_list[skip: skip + limit]]

        @staticmethod
        async def get_chat_search_snippets(chat_ids: List[str], user_id: str, query: str) -> Dict[str, str]:
            return {cid: snippets_map.get(cid, "") for cid in chat_ids}

        @staticmethod
        async def get_chat_by_id_and_user_id(chat_id: str, user_id: str):
            obj = payloads_map.get(chat_id)
            if obj is None:
                return None
            if getattr(obj, "user_id", user_id) != user_id:
                return None
            return obj

    class _FakeChatMessages:
        @staticmethod
        async def get_chat_ids_with_pending_approval(cids: List[str]) -> List[str]:
            return [cid for cid in cids if cid in pending_set]

    class _FakeFolders:
        @staticmethod
        async def get_folders_by_user_id(user_id: str):
            return folders_list

    async def _has_active_tasks(redis: Any, cid: str) -> bool:
        return cid in active_set

    async def _get_response_streams(redis: Any, cid: str) -> List[Dict[str, Any]]:
        return list(streams_map.get(cid, []))

    return {
        "or_": lambda *args: args,
        "select": lambda *cols: _FakeSelectStmt(*cols),
        "text": lambda s: s,
        "get_async_db_context": lambda: _FakeDbCtx(),
        "AutomationRun": _FakeAutomationRun,
        "ChatMessage": _FakeChatMessage,
        "ChatMessages": _FakeChatMessages,
        "Chat": _FakeChat,
        "Chats": _FakeChats,
        "Folders": _FakeFolders,
        "get_response_streams_by_chat_id": _get_response_streams,
        "has_active_tasks": _has_active_tasks,
    }


class TestEchoChatsSecurity(unittest.IsolatedAsyncioTestCase):
    """Axe 1 : Sécurité, validation d'identité et protection anti-IDOR."""

    async def test_rejects_invalid_user_ids(self):
        for bad_uid in ("", "anonymous", "system"):
            res_list = await echo_chats.list_user_chats(user_id=bad_uid)
            self.assertEqual(res_list["status"], "error")
            self.assertEqual(res_list["error_code"], "missing_user")

            res_read = await echo_chats.read_user_chat_transcript(user_id=bad_uid, chat_id="c1")
            self.assertEqual(res_read["status"], "error")
            self.assertEqual(res_read["error_code"], "missing_user")

    async def test_rejects_local_or_empty_chat_id(self):
        for bad_cid in ("", "   ", "local:abc-123"):
            res = await echo_chats.read_user_chat_transcript(user_id="u1", chat_id=bad_cid)
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["error_code"], "invalid_chat_id")

    async def test_excludes_internal_chats_and_enforces_idor(self):
        internal_chat = SimpleNamespace(
            id="c_internal",
            user_id="u1",
            title="Interne",
            meta={"internal": True},
            current_message_id="m1",
            timer_at=None,
            chat={"history": {"currentId": "m1", "messages": {"m1": {"id": "m1", "role": "user", "content": "secret"}}}},
        )
        other_user_chat = SimpleNamespace(
            id="c_other",
            user_id="u2",
            title="Autre",
            meta={},
            current_message_id="m1",
            timer_at=None,
            chat={"history": {"currentId": "m1", "messages": {}}},
        )
        deps = _build_fake_owui_deps(
            chats=[
                {
                    "id": "c_internal",
                    "user_id": "u1",
                    "title": "Interne",
                    "folder_id": None,
                    "archived": False,
                    "pinned": False,
                    "meta": {"internal": True},
                    "current_message_id": "m1",
                    "created_at": 1700000000,
                    "updated_at": 1700000100,
                    "last_read_at": None,
                    "timer_at": None,
                },
                {
                    "id": "c_ok",
                    "user_id": "u1",
                    "title": "Normale",
                    "folder_id": None,
                    "archived": False,
                    "pinned": False,
                    "meta": {},
                    "current_message_id": "m1",
                    "created_at": 1700000000,
                    "updated_at": 1700000050,
                    "last_read_at": None,
                    "timer_at": None,
                },
            ],
            search_hits=["c_internal", "c_ok"],
            chat_payloads={"c_internal": internal_chat, "c_other": other_user_chat},
        )
        with mock.patch.object(echo_chats, "_load_owui_deps", return_value=deps):
            res_list = await echo_chats.list_user_chats(user_id="u1")
            self.assertEqual([c["chat_id"] for c in res_list["chats"]], ["c_ok"])

            res_search = await echo_chats.list_user_chats(user_id="u1", query="secret")
            self.assertEqual([c["chat_id"] for c in res_search["chats"]], ["c_ok"])

            res_int = await echo_chats.read_user_chat_transcript(user_id="u1", chat_id="c_internal")
            self.assertEqual(res_int["status"], "error")
            self.assertEqual(res_int["error_code"], "not_found")

            res_other = await echo_chats.read_user_chat_transcript(user_id="u1", chat_id="c_other")
            self.assertEqual(res_other["status"], "error")
            self.assertEqual(res_other["error_code"], "not_found")


class TestEchoChatsRuntimeState(unittest.IsolatedAsyncioTestCase):
    """Axe 2 : Résolution batch des états runtime canoniques et priorités."""

    async def test_resolve_all_runtime_states_and_priorities(self):
        rows = [
            {"id": "c_llm", "current_message_id": "m1", "timer_at": None},
            {"id": "c_sub", "current_message_id": "m2", "timer_at": None},
            {"id": "c_auto_run", "current_message_id": "m3", "timer_at": None},
            {"id": "c_pending", "current_message_id": "m4", "timer_at": 1700009999},
            {"id": "c_auto_q", "current_message_id": "m5", "timer_at": None},
            {"id": "c_timer", "current_message_id": "m6", "timer_at": 1700001234},
            {"id": "c_err_comp", "current_message_id": "m7", "timer_at": None},
            {"id": "c_err_orphan", "current_message_id": "m8", "timer_at": None},
            {"id": "c_idle", "current_message_id": "m9", "timer_at": None},
        ]
        deps = _build_fake_owui_deps(
            active_task_cids={"c_llm", "c_idle"},
            pending_approval_cids={"c_pending"},
            chat_messages=[
                {"id": "c_llm-m1", "chat_id": "c_llm", "role": "assistant", "done": False, "error": None},
                {"id": "c_err_comp-m7", "chat_id": "c_err_comp", "role": "assistant", "done": True, "error": {"content": "Boom"}},
                {"id": "c_err_orphan-m8", "chat_id": "c_err_orphan", "role": "assistant", "done": False, "error": None},
                {"id": "c_idle-m9", "chat_id": "c_idle", "role": "assistant", "done": True, "error": None},
            ],
            automation_runs=[
                {"chat_id": "c_auto_run", "status": "running"},
                {"chat_id": "c_auto_q", "status": "queued"},
            ],
        )

        sub_key = ("c_sub", "sub_worker_1")
        echo_subagent_hub._RUNNING_SUBAGENTS.add(sub_key)
        try:
            with mock.patch.object(echo_chats, "_load_owui_deps", return_value=deps):
                states = await echo_chats.resolve_chats_runtime_state(rows)
        finally:
            echo_subagent_hub._RUNNING_SUBAGENTS.discard(sub_key)

        self.assertEqual(states["c_llm"]["status"], "running")
        self.assertTrue(states["c_llm"]["runtime"]["llm_running"])

        self.assertEqual(states["c_sub"]["status"], "running")
        self.assertEqual(states["c_sub"]["runtime"]["running_subagents"], 1)

        self.assertEqual(states["c_auto_run"]["status"], "running")
        self.assertEqual(states["c_auto_run"]["runtime"]["automation_status"], "running")

        self.assertEqual(states["c_pending"]["status"], "pending_approval")
        self.assertEqual(states["c_auto_q"]["status"], "queued")
        self.assertEqual(states["c_auto_q"]["runtime"]["automation_status"], "queued")

        self.assertEqual(states["c_timer"]["status"], "queued")
        self.assertEqual(states["c_timer"]["runtime"]["timer_at"], "2023-11-14T22:33:54Z")

        self.assertEqual(states["c_err_comp"]["status"], "error")
        self.assertEqual(states["c_err_orphan"]["status"], "error")
        self.assertEqual(states["c_idle"]["status"], "idle")


class TestListUserChats(unittest.IsolatedAsyncioTestCase):
    """Axe 3 : Recherche, filtres, bornes de dates et pagination de list_user_chats."""

    def _sample_chats(self) -> List[Dict[str, Any]]:
        # 2026-01-10T12:00:00Z = 1768046400, 2026-01-20T12:00:00Z = 1768910400, 2026-01-30T12:00:00Z = 1769774400
        return [
            {
                "id": "c1",
                "user_id": "u1",
                "title": "Alpha",
                "folder_id": "f1",
                "archived": False,
                "pinned": True,
                "meta": {"tags": ["Project_X"]},
                "current_message_id": "m1",
                "created_at": 1768000000,
                "updated_at": 1769774400,
                "last_read_at": 1769700000,
                "timer_at": None,
            },
            {
                "id": "c2",
                "user_id": "u1",
                "title": "Beta",
                "folder_id": "f2",
                "archived": False,
                "pinned": False,
                "meta": {"tags": ["ops"]},
                "current_message_id": "m2",
                "created_at": 1768000000,
                "updated_at": 1768910400,
                "last_read_at": 1768910400,
                "timer_at": None,
            },
            {
                "id": "c3",
                "user_id": "u1",
                "title": "Gamma Archived",
                "folder_id": "f1",
                "archived": True,
                "pinned": False,
                "meta": {"tags": ["project_x"]},
                "current_message_id": "m3",
                "created_at": 1768000000,
                "updated_at": 1768046400,
                "last_read_at": None,
                "timer_at": None,
            },
        ]

    async def test_filters_pagination_and_query_snippets(self):
        deps = _build_fake_owui_deps(
            chats=self._sample_chats(),
            folders=[SimpleNamespace(id="f1", name="Projets"), SimpleNamespace(id="f2", name="Ops")],
            active_task_cids={"c1"},
            chat_messages=[{"id": "c1-m1", "chat_id": "c1", "role": "assistant", "done": False, "error": None}],
            search_hits=["c2", "c1"],
            snippets={"c2": "...extrait beta...", "c1": "...extrait alpha..."},
        )
        with mock.patch.object(echo_chats, "_load_owui_deps", return_value=deps):
            res_all = await echo_chats.list_user_chats(user_id="u1", current_chat_id="c1", limit=1, offset=0)
            self.assertEqual(res_all["status"], "success")
            self.assertEqual(res_all["total_matching"], 2)
            self.assertTrue(res_all["has_more"])
            self.assertEqual(res_all["next_offset"], 1)
            self.assertEqual(res_all["chats"][0]["chat_id"], "c1")
            self.assertTrue(res_all["chats"][0]["is_current"])
            self.assertTrue(res_all["chats"][0]["unread"])
            self.assertEqual(res_all["chats"][0]["folder_name"], "Projets")

            res_p2 = await echo_chats.list_user_chats(user_id="u1", current_chat_id="c1", limit=1, offset=1)
            self.assertEqual([c["chat_id"] for c in res_p2["chats"]], ["c2"])
            self.assertFalse(res_p2["has_more"])
            self.assertIsNone(res_p2["next_offset"])

            res_no_curr = await echo_chats.list_user_chats(user_id="u1", current_chat_id="c1", include_current=False)
            self.assertEqual([c["chat_id"] for c in res_no_curr["chats"]], ["c2"])

            res_status = await echo_chats.list_user_chats(user_id="u1", status="running")
            self.assertEqual([c["chat_id"] for c in res_status["chats"]], ["c1"])

            res_tag_arch = await echo_chats.list_user_chats(
                user_id="u1",
                folder_id="f1",
                tag="Project X",
                include_archived=True,
                start_date="2026-01-01",
                end_date="2026-01-15",
            )
            self.assertEqual([c["chat_id"] for c in res_tag_arch["chats"]], ["c3"])

            res_q = await echo_chats.list_user_chats(user_id="u1", query="extrait")
            self.assertEqual([c["chat_id"] for c in res_q["chats"]], ["c2", "c1"])
            self.assertEqual(res_q["chats"][0]["snippet"], "...extrait beta...")

    async def test_parameter_validation_errors(self):
        for kwargs, code in (
            ({"status": "bogus"}, "invalid_status"),
            ({"limit": 0}, "invalid_limit"),
            ({"limit": 51}, "invalid_limit"),
            ({"offset": -1}, "invalid_offset"),
            ({"start_date": "2026/01/01"}, "invalid_start_date"),
            ({"end_date": "not-a-date"}, "invalid_end_date"),
            ({"start_date": "2026-02-10", "end_date": "2026-02-01"}, "invalid_date_range"),
        ):
            res = await echo_chats.list_user_chats(user_id="u1", **kwargs)
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["error_code"], code)


class TestReadUserChatTranscriptAndHelpers(unittest.IsolatedAsyncioTestCase):
    """Axe 4 : Extraction de lignée, assainissement, flux live, tri, regex et plafond d'octets."""

    def test_extract_chat_branch_active_cycle_and_legacy(self):
        payload = {
            "history": {
                "currentId": "m3",
                "messages": {
                    "m1": {"id": "m1", "parentId": None, "role": "user", "content": "A", "timestamp": 1},
                    "m2": {"id": "m2", "parentId": "m1", "role": "assistant", "content": "B", "timestamp": 2},
                    "m_dead": {"id": "m_dead", "parentId": "m1", "role": "assistant", "content": "DEAD", "timestamp": 5},
                    "m3": {"id": "m3", "parentId": "m2", "role": "user", "content": "C", "timestamp": 3},
                },
            }
        }
        branch = echo_chats.extract_chat_branch(payload)
        self.assertEqual([m["id"] for m in branch], ["m1", "m2", "m3"])

        cyclic_payload = {
            "history": {
                "currentId": "a",
                "messages": {
                    "a": {"id": "a", "parentId": "b", "role": "user", "content": "A"},
                    "b": {"id": "b", "parentId": "a", "role": "assistant", "content": "B"},
                },
            }
        }
        cyclic = echo_chats.extract_chat_branch(cyclic_payload)
        self.assertEqual([m["id"] for m in cyclic], ["b", "a"])

        legacy = echo_chats.extract_chat_branch({"messages": [{"id": "l1", "role": "user", "content": "Legacy"}]})
        self.assertEqual([m["id"] for m in legacy], ["l1"])

    def test_sanitize_transcript_content(self):
        raw = (
            "<think>réflexion secrète</think>\n"
            '<details type="reasoning"><summary>Pensée</summary><details>Imbriquée</details></details>\n'
            '<details type="tool_calls" name="search_web"><details><summary>Args</summary>q</details></details>\n'
            "Voici l'image ![img](data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==) et la réponse finale."
        )
        clean = echo_chats.sanitize_transcript_content(raw)
        self.assertNotIn("réflexion secrète", clean)
        self.assertNotIn("Pensée", clean)
        self.assertNotIn("Args", clean)
        self.assertIn("[Outil: search_web]", clean)
        self.assertIn("[donnée binaire base64 omise]", clean)
        self.assertIn("réponse finale.", clean)

        with_reason = echo_chats.sanitize_transcript_content(raw, include_reasoning=True)
        self.assertIn("réflexion secrète", with_reason)
        self.assertIn('<details type="reasoning">', with_reason)
        self.assertIn("[Outil: search_web]", with_reason)

        with_tools = echo_chats.sanitize_transcript_content(raw, include_tool_calls=True)
        self.assertIn('<details type="tool_calls" name="search_web">', with_tools)
        self.assertNotIn("réflexion secrète", with_tools)

    async def test_read_transcript_live_stream_regex_order_and_bytes_cap(self):
        chat_obj = SimpleNamespace(
            id="c_live",
            user_id="u1",
            title="Live Chat",
            meta={},
            current_message_id="m2",
            timer_at=None,
            chat={
                "history": {
                    "currentId": "m2",
                    "messages": {
                        "m1": {
                            "id": "m1",
                            "parentId": None,
                            "role": "user",
                            "content": "Analyse ce fichier alpha",
                            "timestamp": 1700000000,
                            "files": [{"id": "f1", "name": "rapport.pdf"}],
                        },
                        "m2": {
                            "id": "m2",
                            "parentId": "m1",
                            "role": "assistant",
                            "content": "Début",
                            "done": False,
                            "timestamp": 1700000010,
                        },
                    },
                }
            },
        )
        deps = _build_fake_owui_deps(
            active_task_cids={"c_live"},
            chat_messages=[{"id": "c_live-m2", "chat_id": "c_live", "role": "assistant", "done": False, "error": None}],
            chat_payloads={"c_live": chat_obj},
            response_streams={"c_live": [{"message_id": "m2", "content": "<think>x</think>Streaming alpha en cours"}]},
        )
        with mock.patch.object(echo_chats, "_load_owui_deps", return_value=deps):
            res_new = await echo_chats.read_user_chat_transcript(
                user_id="u1",
                chat_id="current",
                current_chat_id="c_live",
                order="newest_first",
            )
            self.assertEqual(res_new["status"], "success")
            self.assertEqual(res_new["chat_status"], "running")
            self.assertTrue(res_new["is_current"])
            self.assertEqual([m["message_id"] for m in res_new["messages"]], ["m2", "m1"])
            self.assertTrue(res_new["messages"][0]["in_progress"])
            self.assertEqual(res_new["messages"][0]["content"], "Streaming alpha en cours")
            self.assertEqual(res_new["messages"][1]["files"], [{"id": "f1", "name": "rapport.pdf"}])

            res_old_search = await echo_chats.read_user_chat_transcript(
                user_id="u1",
                chat_id="c_live",
                order="oldest_first",
                search_pattern="ALPHA",
            )
            self.assertEqual([m["index"] for m in res_old_search["messages"]], [1, 2])

        # Test plafond max_bytes (plancher effectif 4096 octets)
        big_msgs = {
            f"msg_{i}": {
                "id": f"msg_{i}",
                "parentId": f"msg_{i - 1}" if i > 0 else None,
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message_{i} " + ("Z" * 2400),
                "timestamp": 1700000000 + i,
            }
            for i in range(4)
        }
        big_chat = SimpleNamespace(
            id="c_big",
            user_id="u1",
            title="Big",
            meta={},
            current_message_id="msg_3",
            timer_at=None,
            chat={"history": {"currentId": "msg_3", "messages": big_msgs}},
        )
        deps_big = _build_fake_owui_deps(chat_payloads={"c_big": big_chat})
        with mock.patch.object(echo_chats, "_load_owui_deps", return_value=deps_big):
            res_cap = await echo_chats.read_user_chat_transcript(
                user_id="u1",
                chat_id="c_big",
                order="oldest_first",
                limit=10,
                max_bytes=4096,
            )
            self.assertEqual(res_cap["status"], "success")
            self.assertEqual(res_cap["returned_count"], 1)
            self.assertTrue(res_cap["has_more"])
            self.assertEqual(res_cap["next_offset"], 1)


class TestEchoChatsIntegration(unittest.IsolatedAsyncioTestCase):
    """Axe 5 : Intégration @echo_tool, filtrage sous-agents et guides Tool Guides."""

    async def test_generalist_tools_wrappers(self):
        tools = generalist_mod.Tools()
        chat_obj = SimpleNamespace(
            id="c1",
            user_id="u1",
            title="Conv 1",
            meta={},
            current_message_id="m1",
            timer_at=None,
            chat={
                "history": {
                    "currentId": "m1",
                    "messages": {"m1": {"id": "m1", "role": "user", "content": "Bonjour", "timestamp": 1700000000}},
                }
            },
        )
        deps = _build_fake_owui_deps(
            chats=[
                {
                    "id": "c1",
                    "user_id": "u1",
                    "title": "Conv 1",
                    "folder_id": None,
                    "archived": False,
                    "pinned": False,
                    "meta": {},
                    "current_message_id": "m1",
                    "created_at": 1700000000,
                    "updated_at": 1700000010,
                    "last_read_at": None,
                    "timer_at": None,
                }
            ],
            chat_payloads={"c1": chat_obj},
        )
        with mock.patch.object(echo_chats, "_load_owui_deps", return_value=deps):
            out_list = await tools.list_ui_chats(__user__={"id": "u1"}, __metadata__={"chat_id": "c1"})
            self.assertIsInstance(out_list, dict)
            parsed_list = json.loads(out_list["text"])
            self.assertEqual(parsed_list["status"], "success")
            self.assertEqual(parsed_list["chats"][0]["chat_id"], "c1")

            out_read = await tools.read_ui_chat_transcript(chat_id="c1", __user__={"id": "u1"}, __metadata__={"chat_id": "c1"})
            self.assertIsInstance(out_read, dict)
            parsed_read = json.loads(out_read["text"])
            self.assertEqual(parsed_read["status"], "success")
            self.assertEqual(parsed_read["messages"][0]["content"], "Bonjour")

            out_err = await tools.list_ui_chats(__user__={"id": "anonymous"}, __metadata__={})
            self.assertIsInstance(out_err, dict)
            raw_status = out_err.get("status")
            err_status = raw_status.get("status") if isinstance(raw_status, dict) else raw_status
            self.assertEqual(err_status, "error")
            self.assertIn("❌", out_err["text"])

            out_read_err = await tools.read_ui_chat_transcript(chat_id="local:tmp", __user__={"id": "u1"}, __metadata__={})
            self.assertIsInstance(out_read_err, dict)
            raw_r_status = out_read_err.get("status")
            err_r_status = raw_r_status.get("status") if isinstance(raw_r_status, dict) else raw_r_status
            self.assertEqual(err_r_status, "error")
            self.assertIn("❌", out_read_err["text"])

    def test_subagent_permissions_and_tool_guides_integrity(self):
        excluded = compute_subagent_excluded_tools(
            {"is_subagent": True, "can_delegate": False},
            {"list_ui_chats", "read_ui_chat_transcript", "ask_user_input"},
        )
        self.assertNotIn("list_ui_chats", excluded)
        self.assertNotIn("read_ui_chat_transcript", excluded)
        self.assertIn("ask_user_input", excluded)

        guides = EchoToolGuidesManager.load_all_guides(True)
        self.assertEqual(len(guides), 12)
        self.assertIn("ui_conversations", guides)
        self.assertEqual(
            set(guides["ui_conversations"]["tools"]),
            {"list_ui_chats", "read_ui_chat_transcript", "list_ui_folders", "list_ui_tags"},
        )
        all_tools = [t for info in guides.values() for t in info["tools"]]
        self.assertEqual(len(all_tools), len(set(all_tools)))


if __name__ == "__main__":
    unittest.main()
