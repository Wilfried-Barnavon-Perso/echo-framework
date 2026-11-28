# -*- coding: utf-8 -*-
"""Tests E2E et unitaires pour la consolidation des outils Zone A (v6.24.0.1)."""

from __future__ import annotations

import ast
from contextlib import closing
import copy
import importlib.util
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any, Literal, Optional
from unittest import mock

from pydantic import BaseModel, Field, create_model

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBS_DIR = REPO_ROOT / "14-owui-libs"
TOOLS_DIR = REPO_ROOT / "12-owui-tools"
OWUI_BACKEND_DIR = REPO_ROOT / "16-docker-echo-webui" / "backend"

if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))


def _ensure_open_webui_stubs() -> None:
    """Installe des stubs minimaux compatibles Pydantic pour open_webui.* hors conteneur Docker."""
    if "open_webui.tools.builtin" in sys.modules:
        builtin_mod = sys.modules["open_webui.tools.builtin"]
        existing_task_item = getattr(builtin_mod, "TaskItem", None)
        if isinstance(existing_task_item, type) and issubclass(existing_task_item, BaseModel):
            return

    async def _async_noop(*args, **kwargs):
        return json.dumps({"status": "success"})

    class _TaskItem(BaseModel):
        id: Optional[str] = Field(None, description="Task ID")
        content: str = Field(..., description="Task content")
        status: Literal["pending", "in_progress", "completed", "cancelled"] = Field("pending", description="Status")

    class _FolderForm:
        def __init__(self, name: str = ""):
            self.name = name

    mod_root = sys.modules.get("open_webui") or types.ModuleType("open_webui")
    mod_tools = sys.modules.get("open_webui.tools") or types.ModuleType("open_webui.tools")
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
    ):
        if not hasattr(mod_builtin, fn_name):
            setattr(mod_builtin, fn_name, _async_noop)
    if not hasattr(mod_builtin, "_get_user_tz"):
        mod_builtin._get_user_tz = lambda user_dict: "UTC"
    if not hasattr(mod_builtin, "_ns_to_dt"):
        mod_builtin._ns_to_dt = lambda ns, tz: f"ts:{ns}"
    mod_builtin.TaskItem = _TaskItem

    mod_models = sys.modules.get("open_webui.models") or types.ModuleType("open_webui.models")
    mod_folders = sys.modules.get("open_webui.models.folders") or types.ModuleType("open_webui.models.folders")
    if not hasattr(mod_folders, "Folders"):
        mod_folders.Folders = object
    mod_folders.FolderForm = _FolderForm
    mod_tags = sys.modules.get("open_webui.models.tags") or types.ModuleType("open_webui.models.tags")
    if not hasattr(mod_tags, "Tags"):
        mod_tags.Tags = object
    mod_chats = sys.modules.get("open_webui.models.chats") or types.ModuleType("open_webui.models.chats")
    if not hasattr(mod_chats, "Chats"):
        mod_chats.Chats = object
    if not hasattr(mod_chats, "is_internal_chat"):
        mod_chats.is_internal_chat = lambda *args, **kwargs: False
    mod_auto = sys.modules.get("open_webui.models.automations") or types.ModuleType("open_webui.models.automations")
    if not hasattr(mod_auto, "Automations"):
        mod_auto.Automations = object
    mod_cal = sys.modules.get("open_webui.models.calendar") or types.ModuleType("open_webui.models.calendar")
    if not hasattr(mod_cal, "CalendarEvents"):
        mod_cal.CalendarEvents = object
    mod_utils = sys.modules.get("open_webui.utils") or types.ModuleType("open_webui.utils")
    mod_chat_id = sys.modules.get("open_webui.utils.chat_id") or types.ModuleType("open_webui.utils.chat_id")
    if not hasattr(mod_chat_id, "is_saved_chat_id"):
        mod_chat_id.is_saved_chat_id = lambda cid: bool(cid) and not str(cid).startswith("local:")

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
        sys.modules[k] = v


def _load_module_from_path(name: str, file_path: Path, force_reload: bool = False):
    """Charge un module Python depuis un chemin absolu de façon déterministe."""
    if not force_reload and name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ensure_open_webui_stubs()

import echo_constants  # noqa: E402
import echo_core  # noqa: E402
import echo_crypto  # noqa: E402
import echo_db_schema  # noqa: E402
import echo_paths  # noqa: E402
import echo_prompts  # noqa: E402
import echo_session_clone  # noqa: E402
import echo_state_manager  # noqa: E402
import echo_subagent_hub  # noqa: E402
import echo_terminals  # noqa: E402
from echo_constants import (  # noqa: E402
    DELEGATE_AGENT_BLACKLIST,
    ECHO_AUTOMATION_CONTEXT,
    ECHO_SUBAGENT_CONTEXT,
    ECHO_SUBAGENT_ENGINES_REGISTRY,
    GEMINI_ALLOWED_SCHEMA_KEYS,
    RESOURCE_STATUS_MAP,
    RESOURCE_TYPES,
)
from echo_tool_guides import EchoToolGuidesManager  # noqa: E402

code_executor_mod = _load_module_from_path(
    "zone_a_code_executor_tool", TOOLS_DIR / "code_executor_tool.py", force_reload=True
)
generalist_mod = _load_module_from_path(
    "zone_a_generalist_tools", TOOLS_DIR / "generalist_tools.py", force_reload=True
)
n8n_orch_mod = _load_module_from_path(
    "zone_a_n8n_orchestrator_tool", TOOLS_DIR / "n8n_orchestrator_tool.py", force_reload=True
)
n8n_api_mod = _load_module_from_path(
    "zone_a_n8n_api", REPO_ROOT / "26-docker-n8n-worker" / "n8n_api.py", force_reload=True
)
query_registry_mod = _load_module_from_path(
    "zone_a_query_registry_tool", TOOLS_DIR / "query_registry_tool.py", force_reload=True
)
visual_gen_mod = _load_module_from_path(
    "zone_a_universal_visual_generator", TOOLS_DIR / "universal_visual_generator.py", force_reload=True
)
owui_tool_calling = _load_module_from_path(
    "zone_a_owui_tool_calling", OWUI_BACKEND_DIR / "open_webui" / "utils" / "tool_calling.py", force_reload=True
)


def _inline_json_schema_refs(node: Any, defs: dict) -> Any:
    """Résout récursivement les `$ref` et simplifie `anyOf` nullable comme le pipeline OWUI."""
    if isinstance(node, list):
        return [_inline_json_schema_refs(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    out = copy.deepcopy(node)
    if "$ref" in out:
        ref_key = out.pop("$ref").split("/")[-1]
        if ref_key in defs:
            resolved = _inline_json_schema_refs(defs[ref_key], defs)
            for k, v in out.items():
                resolved.setdefault(k, v)
            out = resolved

    if "anyOf" in out:
        non_null = [t for t in out["anyOf"] if not (isinstance(t, dict) and t.get("type") == "null")]
        if len(non_null) == 1:
            picked = _inline_json_schema_refs(non_null[0], defs)
            del out["anyOf"]
            out.update(picked)
        else:
            out["anyOf"] = [_inline_json_schema_refs(t, defs) for t in non_null]

    if "properties" in out and isinstance(out["properties"], dict):
        out["properties"] = {
            k: _inline_json_schema_refs(v, defs) for k, v in out["properties"].items()
        }
    if "items" in out and isinstance(out["items"], dict):
        out["items"] = _inline_json_schema_refs(out["items"], defs)
    return out


def _clean_gemini_schema_exact(schema: dict) -> dict:
    """Reproduit exactement `clean_gemini_schema` de `echo_gemini_client.py`."""
    if not isinstance(schema, dict):
        return schema
    for k in [key for key in list(schema.keys()) if key not in GEMINI_ALLOWED_SCHEMA_KEYS]:
        schema.pop(k, None)
    if "properties" in schema and isinstance(schema["properties"], dict):
        for v in schema["properties"].values():
            _clean_gemini_schema_exact(v)
    if "items" in schema and isinstance(schema["items"], dict):
        _clean_gemini_schema_exact(schema["items"])
    return schema


def _build_cleaned_gemini_tool_schema(method) -> dict:
    """Construit le schéma Pydantic d'une méthode @echo_tool puis le passe dans clean_gemini_schema."""
    import inspect
    from typing import get_type_hints

    raw_fn = getattr(method, "__wrapped__", method)
    hints = get_type_hints(raw_fn)
    sig = inspect.signature(raw_fn)
    fields = {}
    for pname, param in sig.parameters.items():
        if pname == "self" or (pname.startswith("__") and pname.endswith("__")):
            continue
        ann = hints.get(pname, Any)
        default = param.default if param.default is not inspect.Parameter.empty else ...
        fields[pname] = (ann, default)

    model_cls = create_model(raw_fn.__name__, **fields)
    raw_json_schema = model_cls.model_json_schema()
    defs = raw_json_schema.pop("$defs", {})
    inlined = _inline_json_schema_refs(raw_json_schema, defs)
    return _clean_gemini_schema_exact(inlined)


def _assert_all_params_have_type(schema_node: dict, path: str = "root") -> None:
    """Vérifie récursivement qu'aucun paramètre ou sous-champ n'est dépourvu de `'type'`."""
    assert "type" in schema_node, f"Champ sans 'type' détecté à {path}: {schema_node}"
    if schema_node.get("type") == "object":
        for prop_name, prop_val in (schema_node.get("properties") or {}).items():
            _assert_all_params_have_type(prop_val, f"{path}.{prop_name}")
    elif schema_node.get("type") == "array" and isinstance(schema_node.get("items"), dict):
        _assert_all_params_have_type(schema_node["items"], f"{path}[]")


class TestAstCountAndSchemas(unittest.TestCase):
    """(a) Compte AST exact = 87 @echo_tool, Pydantic, clean_gemini_schema et SSOT."""

    def test_exact_echo_tool_ast_counts(self) -> None:
        counts = {}
        for py_file in sorted(TOOLS_DIR.glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            cnt = 0
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if any(isinstance(d, ast.Name) and d.id == "echo_tool" for d in node.decorator_list):
                        cnt += 1
            counts[py_file.name] = cnt

        self.assertEqual(counts["code_executor_tool.py"], 3)
        self.assertEqual(counts["generalist_tools.py"], 12)
        self.assertEqual(counts["n8n_orchestrator_tool.py"], 5)
        self.assertEqual(counts["universal_visual_generator.py"], 3)
        self.assertEqual(sum(counts.values()), 87)

    def test_ssot_constants_and_prompts_updated(self) -> None:
        self.assertEqual(RESOURCE_TYPES.get("generated"), "generated")
        self.assertIn("generated", RESOURCE_STATUS_MAP)
        self.assertEqual(
            RESOURCE_STATUS_MAP["generated"],
            list(echo_constants.FILE_INGESTION_STATUS.values()),
        )
        self.assertEqual(RESOURCE_TYPES.get("terminal"), "terminal")
        self.assertEqual(
            RESOURCE_STATUS_MAP.get("terminal"),
            ["active_unlocked", "active_locked", "closed"],
        )

        n8n_allowed = ECHO_SUBAGENT_ENGINES_REGISTRY["n8n_grapher"]["default_allowed_tools"]
        for expected in ("manage_n8n_workflow", "manage_n8n_template", "n8n_hub", "query_n8n_documentation"):
            self.assertIn(expected, n8n_allowed)
        for legacy in (
            "prepare_n8n_workflow",
            "run_n8n_oneshot_workflow",
            "deploy_n8n_daemon",
            "modify_n8n_workflow",
            "delete_n8n_workflow",
            "create_n8n_template",
            "modify_n8n_template",
            "delete_n8n_template",
            "search_n8n_hub",
            "download_n8n_hub_template",
        ):
            self.assertNotIn(legacy, n8n_allowed)

        self.assertIn("manage_ui_tasks", DELEGATE_AGENT_BLACKLIST)
        self.assertIn("close_image_session", DELEGATE_AGENT_BLACKLIST)
        self.assertNotIn("create_ui_tasks", DELEGATE_AGENT_BLACKLIST)
        self.assertNotIn("update_ui_task", DELEGATE_AGENT_BLACKLIST)

        self.assertIn("n8n_hub", echo_prompts.SYS_ORCHESTRATOR_N8N_GRAPHER)
        self.assertNotIn("search_n8n_hub", echo_prompts.SYS_ORCHESTRATOR_N8N_GRAPHER)
        self.assertNotIn("download_n8n_hub_template", echo_prompts.SYS_ORCHESTRATOR_N8N_GRAPHER)
        self.assertIn("max 180s", generalist_mod.Tools.wait_timer.__doc__)

    def test_pydantic_and_clean_gemini_schema_validity(self) -> None:
        gen_tools = generalist_mod.Tools()
        n8n_tools = n8n_orch_mod.Tools()
        exec_tools = code_executor_mod.Tools()

        expected_enums = {
            (gen_tools.manage_ui_calendar, "action"): ["list", "read", "create", "update", "delete"],
            (gen_tools.manage_ui_automations, "action"): ["list", "read", "create", "update", "toggle", "delete"],
            (gen_tools.manage_ui_automations, "target_mode"): ["new_chat", "existing_chat", "persistent_chat"],
            (gen_tools.manage_ui_tasks, "action"): ["create", "update"],
            (gen_tools.manage_ui_tasks, "status"): ["pending", "in_progress", "completed", "cancelled"],
            (n8n_tools.manage_n8n_template, "action"): ["create", "update", "delete"],
            (n8n_tools.manage_n8n_workflow, "action"): ["prepare", "update", "stop", "delete", "run_oneshot", "deploy_daemon"],
            (n8n_tools.n8n_hub, "action"): ["search", "download"],
            (exec_tools.control_sandbox_execution, "action"): ["status", "listen", "kill"],
            (exec_tools.manage_terminal, "action"): ["exec", "open", "list", "read", "write", "close"],
            (exec_tools.manage_terminal, "mode"): ["local", "ssh"],
            (exec_tools.manage_terminal, "signal_key"): ["ctrl_c", "ctrl_d", "ctrl_z", "enter"],
        }

        for method in (
            gen_tools.manage_ui_calendar,
            gen_tools.manage_ui_automations,
            gen_tools.manage_ui_tasks,
            n8n_tools.manage_n8n_template,
            n8n_tools.manage_n8n_workflow,
            n8n_tools.n8n_hub,
            exec_tools.control_sandbox_execution,
            exec_tools.manage_terminal,
        ):
            cleaned = _build_cleaned_gemini_tool_schema(method)
            _assert_all_params_have_type(cleaned, path=method.__name__)

        for (method, param_name), exp_vals in expected_enums.items():
            cleaned = _build_cleaned_gemini_tool_schema(method)
            prop = cleaned["properties"][param_name]
            self.assertEqual(prop.get("enum"), exp_vals, f"{method.__name__}.{param_name}")


class TestToolCallingCoercion(unittest.IsolatedAsyncioTestCase):
    """(b) Coercition réelle via `open_webui.utils.tool_calling.prepare_tool_call`."""

    async def test_prepare_tool_call_coercion_and_normalize_id_list(self) -> None:
        cal_schema = _build_cleaned_gemini_tool_schema(generalist_mod.Tools().manage_ui_calendar)

        cases = [
            ("id-1", ["id-1"]),
            ("id-1,id-2", ["id-1", "id-2"]),
            ('["id-1","id-2"]', ["id-1", "id-2"]),
            (["id-1", "id-2"], ["id-1", "id-2"]),
        ]
        for raw_ids, expected_ids in cases:
            call_obj = {
                "name": "manage_ui_calendar",
                "arguments": json.dumps({"action": "delete", "event_ids": raw_ids}),
            }
            err = owui_tool_calling.prepare_tool_call(call_obj, schema=cal_schema)
            self.assertIsNone(err)
            parsed_args = json.loads(call_obj["arguments"])
            if raw_ids == '["id-1","id-2"]':
                self.assertEqual(parsed_args["event_ids"], ["id-1", "id-2"])
            self.assertEqual(generalist_mod.Tools._normalize_id_list(parsed_args["event_ids"]), expected_ids)

            mock_del = mock.AsyncMock(return_value=json.dumps({"status": "success"}))
            with mock.patch.object(generalist_mod, "_owui_delete_cal", mock_del):
                res = await generalist_mod.Tools().manage_ui_calendar(
                    **parsed_args,
                    __user__={"id": "u1"},
                    __metadata__={"chat_id": "c1"},
                )
            self.assertEqual(res["status"]["status"], "success")
            self.assertEqual(mock_del.await_count, len(expected_ids))


class TestConsolidatedGeneralistAndSandboxTools(unittest.IsolatedAsyncioTestCase):
    """(c) E2E complet manage_ui_calendar, manage_ui_automations, manage_ui_tasks, control_sandbox_execution."""

    def setUp(self) -> None:
        self.tools = generalist_mod.Tools()
        self.user = {"id": "user-1", "timezone": "UTC"}
        self.meta = {"chat_id": "chat-1"}

    async def test_manage_ui_calendar_e2e_and_pas_guards(self) -> None:
        mock_search = mock.AsyncMock(return_value=json.dumps({"events": [], "total": 0}))
        mock_create = mock.AsyncMock(return_value=json.dumps({"status": "success", "id": "ev-1"}))
        mock_update = mock.AsyncMock(return_value=json.dumps({"status": "success", "id": "ev-1"}))
        mock_delete = mock.AsyncMock(return_value=json.dumps({"status": "success"}))

        ev_own = types.SimpleNamespace(
            id="ev-1",
            calendar_id="cal-1",
            user_id="user-1",
            title="Reunion",
            description="Desc",
            start_at=100,
            end_at=200,
            all_day=False,
            rrule="",
            location="Paris",
            color="",
            is_cancelled=False,
            attendees=[],
            meta={"alert_minutes": 15},
            created_at=1,
            updated_at=2,
        )
        ev_other = types.SimpleNamespace(id="ev-2", user_id="other-user")

        async def _fake_get_event(eid: str):
            return {"ev-1": ev_own, "ev-2": ev_other}.get(eid)

        fake_cal_events = types.SimpleNamespace(get_event_by_id=_fake_get_event)

        with (
            mock.patch.object(generalist_mod, "_owui_search", mock_search),
            mock.patch.object(generalist_mod, "_owui_create_cal", mock_create),
            mock.patch.object(generalist_mod, "_owui_update_cal", mock_update),
            mock.patch.object(generalist_mod, "_owui_delete_cal", mock_delete),
            mock.patch.object(generalist_mod, "CalendarEvents", fake_cal_events),
        ):
            # list + garde PAS
            res_list = await self.tools.manage_ui_calendar(
                action="list", query="Reu", start="2026-01-01", limit=10, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_list["status"]["status"], "success")
            mock_search.assert_awaited_once()

            bad_list = await self.tools.manage_ui_calendar(
                action="list", title="X", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(bad_list["status"]["status"], "error")

            # read multi-tenant
            res_read = await self.tools.manage_ui_calendar(
                action="read", event_ids='["ev-1","ev-2","ev-missing"]', __user__=self.user, __metadata__=self.meta
            )
            parsed_read = json.loads(res_read["text"])
            self.assertEqual(parsed_read["total_found"], 1)
            self.assertEqual(parsed_read["events"][0]["reminder_minutes"], 15)
            self.assertEqual(parsed_read["not_found"], ["ev-2", "ev-missing"])

            # create (all_day None -> False, reminder_minutes transmis)
            res_create = await self.tools.manage_ui_calendar(
                action="create",
                title="Rappel",
                start="2026-05-01 09:00",
                reminder_minutes=0,
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(res_create["status"]["status"], "success")
            self.assertIs(mock_create.call_args.kwargs["all_day"], False)
            self.assertEqual(mock_create.call_args.kwargs["reminder_minutes"], 0)

            bad_create = await self.tools.manage_ui_calendar(
                action="create", title="", start="", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(bad_create["status"]["status"], "error")

            # update exige exactement 1 ID
            bad_upd = await self.tools.manage_ui_calendar(
                action="update", event_ids=["ev-1", "ev-2"], title="Maj", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(bad_upd["status"]["status"], "error")

            res_upd = await self.tools.manage_ui_calendar(
                action="update", event_ids=["ev-1"], title="Maj", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_upd["status"]["status"], "success")
            self.assertEqual(mock_update.call_args.kwargs["event_id"], "ev-1")

            # delete 1..N
            res_del = await self.tools.manage_ui_calendar(
                action="delete", event_ids="ev-1,ev-3", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_del["status"]["status"], "success")
            self.assertEqual(res_del["status"]["succeeded_count"], 2)

    async def test_manage_ui_calendar_chronological_bounds(self) -> None:
        mock_search = mock.AsyncMock(return_value=json.dumps({"events": [], "total": 0}))
        mock_create = mock.AsyncMock(return_value=json.dumps({"status": "success", "id": "ev-1"}))
        mock_update = mock.AsyncMock(return_value=json.dumps({"status": "success", "id": "ev-1"}))

        # 2026-05-01T10:00:00Z .. 2026-05-01T12:00:00Z en nanosecondes
        ev_existing = types.SimpleNamespace(
            id="ev-1",
            user_id="user-1",
            start_at=1_777_629_600_000_000_000,
            end_at=1_777_636_800_000_000_000,
        )

        async def _fake_get_event(eid: str):
            return ev_existing if eid == "ev-1" else None

        fake_cal_events = types.SimpleNamespace(get_event_by_id=_fake_get_event)

        with (
            mock.patch.object(generalist_mod, "_owui_search", mock_search),
            mock.patch.object(generalist_mod, "_owui_create_cal", mock_create),
            mock.patch.object(generalist_mod, "_owui_update_cal", mock_update),
            mock.patch.object(generalist_mod, "CalendarEvents", fake_cal_events),
        ):
            # create avec end < start
            bad_create = await self.tools.manage_ui_calendar(
                action="create",
                title="Invalide",
                start="2026-05-01 12:00",
                end="2026-05-01 10:00",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(bad_create["status"]["status"], "error")
            self.assertEqual(bad_create["status"]["error_code"], "invalid_arguments")
            mock_create.assert_not_awaited()

            # list avec end < start
            bad_list = await self.tools.manage_ui_calendar(
                action="list",
                start="2026-05-02T10:00:00Z",
                end="2026-05-01T10:00:00Z",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(bad_list["status"]["status"], "error")
            self.assertEqual(bad_list["status"]["error_code"], "invalid_arguments")
            mock_search.assert_not_awaited()

            # update complet avec start et end inversés
            bad_upd_full = await self.tools.manage_ui_calendar(
                action="update",
                event_ids=["ev-1"],
                start="2026-05-01T14:00:00Z",
                end="2026-05-01T13:00:00Z",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(bad_upd_full["status"]["status"], "error")
            self.assertEqual(bad_upd_full["status"]["error_code"], "invalid_arguments")

            # update partiel : end seul antérieur à existing_ev.start_at
            bad_upd_end = await self.tools.manage_ui_calendar(
                action="update",
                event_ids=["ev-1"],
                end="2026-05-01T09:00:00Z",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(bad_upd_end["status"]["status"], "error")
            self.assertEqual(bad_upd_end["status"]["error_code"], "invalid_arguments")

            # update partiel : start seul postérieur à existing_ev.end_at
            bad_upd_start = await self.tools.manage_ui_calendar(
                action="update",
                event_ids=["ev-1"],
                start="2026-05-01T13:00:00Z",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(bad_upd_start["status"]["status"], "error")
            self.assertEqual(bad_upd_start["status"]["error_code"], "invalid_arguments")
            mock_update.assert_not_awaited()

            # create avec end == start accepté
            ok_eq = await self.tools.manage_ui_calendar(
                action="create",
                title="Ponctuel",
                start="2026-05-01 10:00",
                end="2026-05-01 10:00",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(ok_eq["status"]["status"], "success")
            mock_create.assert_awaited_once()

    async def test_manage_ui_automations_e2e_and_pas_guards(self) -> None:
        mock_create = mock.AsyncMock(return_value=json.dumps({"status": "success", "id": "auto-1"}))
        mock_update = mock.AsyncMock(return_value=json.dumps({"status": "success", "id": "auto-1"}))
        mock_toggle = mock.AsyncMock(return_value=json.dumps({"status": "success", "is_active": False}))
        mock_delete = mock.AsyncMock(return_value=json.dumps({"status": "success"}))

        fake_folders = types.SimpleNamespace(
            get_folders_by_user_id=mock.AsyncMock(return_value=[types.SimpleNamespace(id="fld-1", name="Automations")])
        )
        fake_chats = types.SimpleNamespace(
            get_chat_by_id_and_user_id=mock.AsyncMock(
                return_value=types.SimpleNamespace(id="chat-existing", meta={})
            )
        )
        auto_record = types.SimpleNamespace(
            id="auto-1",
            user_id="user-1",
            name="Veille",
            folder_id="fld-1",
            data={
                "prompt": "Check",
                "model_id": echo_constants.OWUI_ORCHESTRATOR_MODEL,
                "rrule": "RRULE:FREQ=DAILY",
                "target": {"type": "persistent_chat", "chat_id": "chat-existing"},
            },
            is_active=True,
            last_run_at=None,
            next_run_at=1234,
            created_at=1,
            updated_at=2,
        )
        fake_automations = types.SimpleNamespace(
            search_automations=mock.AsyncMock(return_value=types.SimpleNamespace(items=[auto_record])),
            get_by_id=mock.AsyncMock(return_value=auto_record),
        )

        with (
            mock.patch.object(generalist_mod, "_owui_create_auto", mock_create),
            mock.patch.object(generalist_mod, "_owui_update_auto", mock_update),
            mock.patch.object(generalist_mod, "_owui_toggle_auto", mock_toggle),
            mock.patch.object(generalist_mod, "_owui_delete_auto", mock_delete),
            mock.patch.object(generalist_mod, "Folders", fake_folders),
            mock.patch.object(generalist_mod, "Chats", fake_chats),
            mock.patch.object(generalist_mod, "Automations", fake_automations),
        ):
            # list
            res_list = await self.tools.manage_ui_automations(
                action="list", query="Veille", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(len(json.loads(res_list["text"])), 1)

            # read
            res_read = await self.tools.manage_ui_automations(
                action="read", automation_ids=["auto-1"], __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(json.loads(res_read["text"])["total_found"], 1)

            # create existing_chat with current vs local:*
            res_cur = await self.tools.manage_ui_automations(
                action="create",
                name="Cron 1",
                prompt="Run",
                rrule="RRULE:FREQ=DAILY",
                target_mode="existing_chat",
                target_chat_id="current",
                __user__=self.user,
                __metadata__={"chat_id": "chat-1"},
            )
            self.assertEqual(res_cur["status"]["status"], "success")
            self.assertEqual(mock_create.call_args.kwargs["target_chat_id"], "chat-1")
            self.assertEqual(mock_create.call_args.kwargs["model_id"], echo_constants.OWUI_ORCHESTRATOR_MODEL)

            res_local_err = await self.tools.manage_ui_automations(
                action="create",
                name="Cron local",
                prompt="Run",
                rrule="RRULE:FREQ=DAILY",
                target_mode="existing_chat",
                target_chat_id="current",
                __user__=self.user,
                __metadata__={"chat_id": "local:tmp-123"},
            )
            self.assertEqual(res_local_err["status"]["status"], "error")

            # create persistent_chat refuse target_chat_id
            res_pers_err = await self.tools.manage_ui_automations(
                action="create",
                name="Cron pers",
                prompt="Run",
                rrule="RRULE:FREQ=DAILY",
                target_mode="persistent_chat",
                target_chat_id="chat-1",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(res_pers_err["status"]["status"], "error")

            # update with reset -> persistent_chat & target_chat_id=None
            res_reset = await self.tools.manage_ui_automations(
                action="update",
                automation_ids=["auto-1"],
                target_mode="persistent_chat",
                target_chat_id="reset",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(res_reset["status"]["status"], "success")
            self.assertEqual(mock_update.call_args.kwargs["target_type"], "persistent_chat")
            self.assertIsNone(mock_update.call_args.kwargs["target_chat_id"])

            # toggle & delete batch
            res_toggle = await self.tools.manage_ui_automations(
                action="toggle", automation_ids=["auto-1", "auto-2"], __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_toggle["status"]["succeeded_count"], 2)

            res_del = await self.tools.manage_ui_automations(
                action="delete", automation_ids=["auto-1"], __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_del["status"]["status"], "success")

    async def test_manage_ui_tasks_e2e_and_headless_guard(self) -> None:
        mock_create_tasks = mock.AsyncMock(return_value=json.dumps({"tasks": [{"id": "1", "content": "Etape 1"}]}))
        mock_update_task = mock.AsyncMock(return_value=json.dumps({"tasks": [{"id": "1", "status": "completed"}]}))

        with (
            mock.patch.object(generalist_mod, "_owui_create_tasks", mock_create_tasks),
            mock.patch.object(generalist_mod, "_owui_update_task", mock_update_task),
        ):
            # Garde headless sous-agent
            tok = ECHO_SUBAGENT_CONTEXT.set({"is_subagent": True, "sub_sid": "dlg_1"})
            try:
                res_hl = await self.tools.manage_ui_tasks(
                    action="create", tasks=[{"content": "A"}], __user__=self.user, __metadata__=self.meta
                )
            finally:
                ECHO_SUBAGENT_CONTEXT.reset(tok)
            self.assertEqual(res_hl["status"]["error_code"], "headless_forbidden")

            # Garde PAS croisée create avec task_id
            res_bad_c = await self.tools.manage_ui_tasks(
                action="create", tasks=[{"content": "A"}], task_id="1", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_bad_c["status"]["status"], "error")

            # Payload invalide
            res_inv = await self.tools.manage_ui_tasks(
                action="create", tasks=[{"title": "   "}], __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_inv["status"]["error_code"], "invalid_tasks_payload")

            # Normalisation aliases -> content
            res_ok = await self.tools.manage_ui_tasks(
                action="create",
                tasks=[{"title": "Etape 1"}, {"label": "Etape 2", "status": "in_progress"}],
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(res_ok["status"]["status"], "success")
            self.assertEqual(
                mock_create_tasks.call_args.kwargs["tasks"],
                [{"content": "Etape 1"}, {"content": "Etape 2", "status": "in_progress"}],
            )

            # Update exige status explicite + garde PAS tasks
            res_bad_u = await self.tools.manage_ui_tasks(
                action="update", task_id="1", tasks=[{"content": "X"}], __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_bad_u["status"]["status"], "error")

            res_missing_st = await self.tools.manage_ui_tasks(
                action="update", task_id="1", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_missing_st["status"]["status"], "error")
            self.assertEqual(res_missing_st["status"]["error_code"], "invalid_arguments")
            mock_update_task.assert_not_awaited()

            res_u = await self.tools.manage_ui_tasks(
                action="update", task_id="1", status="completed", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_u["status"]["status"], "success")
            self.assertEqual(mock_update_task.call_args.kwargs["status"], "completed")

    async def test_control_sandbox_execution_kill_and_no_legacy(self) -> None:
        self.assertFalse(hasattr(code_executor_mod.Tools, "kill_sandbox_execution"))

        class _FakeWM:
            def __init__(self):
                self._sandbox_run_owners = {
                    "u1:c1:run_owned": "sub_1",
                    "u1:c1:run_other": "sub_2",
                }
                self.popped = []
                self.cleared = []

            def is_run_owned_by_subagent(self, uid, cid, rid, sub_sid):
                return self._sandbox_run_owners.get(f"{uid}:{cid}:{rid}") == sub_sid

            def pop_sandbox_run_owner(self, uid, cid, rid):
                self.popped.append(rid)

            def clear_sandbox_stream_offsets(self, uid, cid, rid):
                self.cleared.append(rid)

        fake_wm = _FakeWM()
        post_calls = []

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                post_calls.append((url, json))
                rid = (json or {}).get("run_id") or "run_all"
                return types.SimpleNamespace(
                    json=lambda: {"status": "killed", "run_ids": [rid], "pids": [4242]}
                )

        exec_tools = code_executor_mod.Tools()
        with (
            mock.patch.object(code_executor_mod, "get_window_manager", return_value=fake_wm),
            mock.patch("httpx.AsyncClient", _FakeClient),
        ):
            tok = ECHO_SUBAGENT_CONTEXT.set({"is_subagent": True, "sub_sid": "sub_1"})
            try:
                denied = await exec_tools.control_sandbox_execution(
                    action="kill", run_id="run_other", __user__={"id": "u1"}, __metadata__={"chat_id": "c1"}
                )
                self.assertEqual(denied["status"]["status"], "error")

                res_sub = await exec_tools.control_sandbox_execution(
                    action="kill", __user__={"id": "u1"}, __metadata__={"chat_id": "c1"}
                )
            finally:
                ECHO_SUBAGENT_CONTEXT.reset(tok)

        self.assertEqual(res_sub["status"]["status"], "killed")
        self.assertEqual(res_sub["status"]["run_ids"], ["run_owned"])
        self.assertEqual(fake_wm.popped, ["run_owned"])
        self.assertEqual(len(post_calls), 1)
        self.assertEqual(post_calls[0][1]["run_id"], "run_owned")

    async def test_manage_terminal_and_registry_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            users_root = os.path.join(tmp_dir, "users")
            os.makedirs(users_root, exist_ok=True)
            uid = "user_term"
            cid = "chat_term"
            ghost_cid = "chat_ghost"

            class _FakeWM:
                def __init__(self) -> None:
                    self.opened_tabs: list[tuple[str, str, str, dict]] = []
                    self.terminal_states: list[tuple[str, str, dict, str]] = []

                async def open_tab(self, u: str, c: str, kind: str, payload: dict) -> None:
                    self.opened_tabs.append((u, c, kind, payload))

                async def broadcast_terminal_state(self, u: str, c: str, terminal_data: dict, action: str = "updated") -> None:
                    self.terminal_states.append((u, c, terminal_data, action))

            class _FakeResponse:
                def __init__(self, status_code: int, payload: dict) -> None:
                    self.status_code = status_code
                    self._payload = payload
                    self.text = json.dumps(payload, ensure_ascii=False)

                def json(self) -> dict:
                    return self._payload

            class _FakeTerminalWorkerClient:
                def __init__(self) -> None:
                    self.sessions: dict[str, dict] = {}
                    self.ssh_exec_calls: list[dict] = []
                    self.local_exec_calls: list[dict] = []
                    self._seq = 0

                async def get(self, url: str, params=None, headers=None, timeout=None):
                    if url.endswith("/api/terminals"):
                        req_cid = (params or {}).get("chat_id") or ""
                        items = [dict(s) for s in self.sessions.values() if s["chat_id"] == req_cid and s["alive"]]
                        return _FakeResponse(200, {"items": items, "count": len(items)})
                    if "/buffer" in url:
                        sid = url.split("/api/terminals/")[1].split("/buffer")[0]
                        sess = self.sessions.get(sid)
                        if not sess:
                            return _FakeResponse(404, {"error": "Terminal session not found"})
                        return _FakeResponse(200, {
                            "session": dict(sess),
                            "text": f"output:{sess['name']}",
                            "cursor": sess["cursor"],
                            "truncated": False,
                            "total_buffered_bytes": 16,
                        })
                    return _FakeResponse(404, {"error": "Unknown GET"})

                async def post(self, url: str, json=None, headers=None, timeout=None):
                    data = json or {}
                    if url.endswith("/execute"):
                        self.local_exec_calls.append(data)
                        cmd = data.get("command") or ""
                        if "SLEEP_TIMEOUT" in cmd:
                            return _FakeResponse(200, {
                                "status": "error",
                                "state": "timeout",
                                "exit_code": None,
                                "output": "",
                                "error": "Timeout (5s) dépassé.",
                            })
                        return _FakeResponse(200, {
                            "status": "success",
                            "state": "completed",
                            "exit_code": 0,
                            "output": f"local:{cmd}",
                            "error": "",
                        })
                    if url.endswith("/ssh"):
                        self.ssh_exec_calls.append(data)
                        return _FakeResponse(200, {"status": "success", "exit_code": 0, "output": "ok-cert", "error": ""})
                    if url.endswith("/api/terminals"):
                        self._seq += 1
                        sid = f"term_{self._seq:012x}"
                        mode = data.get("mode") or "local"
                        acc = data.get("account_id") or ""
                        ssh_cfg = data.get("ssh_config") or {}
                        target = (
                            f"{ssh_cfg.get('user')}@{ssh_cfg.get('host')}:{ssh_cfg.get('port')}"
                            if mode == "ssh"
                            else "local:/sandbox"
                        )
                        sess = {
                            "id": sid,
                            "chat_id": data.get("chat_id") or "",
                            "user_id": data.get("user_id") or "",
                            "name": data.get("name") or ("Terminal" if mode == "local" else acc),
                            "mode": mode,
                            "account_id": acc,
                            "target": target,
                            "alive": True,
                            "agent_locked": True if mode == "ssh" else bool(data.get("agent_locked", False)),
                            "cursor": 10,
                        }
                        self.sessions[sid] = sess
                        return _FakeResponse(200, dict(sess))
                    if "/input" in url:
                        sid = url.split("/api/terminals/")[1].split("/input")[0]
                        sess = self.sessions.get(sid)
                        if not sess:
                            return _FakeResponse(404, {"error": "Terminal session not found"})
                        if data.get("actor", "agent") != "user" and sess["agent_locked"]:
                            return _FakeResponse(423, {
                                "status": "error",
                                "error_code": "terminal_locked",
                                "session_id": sid,
                                "name": sess["name"],
                                "agent_locked": True,
                                "error": "Session terminale verrouillée en lecture seule.",
                            })
                        sess["cursor"] += 5
                        return _FakeResponse(200, {
                            "status": "success",
                            "session": dict(sess),
                            "text": f"wrote:{data.get('input') or data.get('signal')}",
                            "cursor": sess["cursor"],
                            "truncated": False,
                        })
                    return _FakeResponse(404, {"error": "Unknown POST"})

                async def patch(self, url: str, json=None, headers=None, timeout=None):
                    sid = url.split("/api/terminals/")[1].split("/lock")[0]
                    sess = self.sessions.get(sid)
                    if not sess:
                        return _FakeResponse(404, {"error": "Terminal session not found"})
                    sess["agent_locked"] = bool((json or {}).get("agent_locked"))
                    return _FakeResponse(200, dict(sess))

                async def delete(self, url: str, params=None, headers=None, timeout=None):
                    sid = url.split("/api/terminals/")[1]
                    sess = self.sessions.get(sid)
                    if not sess:
                        return _FakeResponse(404, {"error": "Terminal session not found"})
                    if (params or {}).get("actor") == "agent" and sess["agent_locked"]:
                        return _FakeResponse(423, {
                            "status": "error",
                            "error_code": "terminal_locked",
                            "session_id": sid,
                            "name": sess["name"],
                            "agent_locked": True,
                            "error": "Session terminale verrouillée : fermeture interdite à l'agent.",
                        })
                    sess["alive"] = False
                    return _FakeResponse(200, {"status": "terminated", "id": sid})

            fake_wm = _FakeWM()
            fake_worker = _FakeTerminalWorkerClient()
            exec_tools = code_executor_mod.Tools()
            reg_tools = query_registry_mod.Tools()

            with (
                mock.patch.dict(os.environ, {"ECHO_VAULT_ENCRYPTION_KEY": "test-zone-a-master-key-32bytes!!"}),
                mock.patch.object(echo_constants, "ECHO_USERS_ROOT", users_root),
                mock.patch.object(echo_paths, "ECHO_USERS_ROOT", users_root),
                mock.patch.object(echo_state_manager, "ECHO_USERS_ROOT", users_root),
                mock.patch.object(echo_terminals, "_get_global_client", return_value=fake_worker),
                mock.patch.object(code_executor_mod, "get_window_manager", return_value=fake_wm),
                mock.patch.object(query_registry_mod.EchoCompactionEngine, "load_active_branch_ids", new=mock.AsyncMock(return_value=[])),
            ):
                echo_crypto._SCOPED_KEY_CACHE.clear()
                try:
                    echo_terminals.ensure_echo_session_environment(uid, cid)
                    echo_state_manager.EchoStateManager(user_id=uid).save_vault_credential(
                        "ssh",
                        "vps-prod",
                        json.dumps({
                            "host": "192.168.1.50",
                            "port": "2222",
                            "user": "ubuntu",
                            "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nKEY\n-----END OPENSSH PRIVATE KEY-----",
                            "certificate": "ssh-ed25519-cert-v01@openssh.com CERTDATA",
                            "description": "Prod VPS",
                        }),
                    )

                    # 1. exec SSH avec certificat + exec local (défaut & sous-agent) + gardes d'arguments
                    res_exec = await exec_tools.manage_terminal(
                        action="exec",
                        account_id="vps-prod",
                        command="uname -a",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_exec["status"]["status"], "success")
                    self.assertEqual(res_exec["status"]["mode"], "ssh")
                    self.assertEqual(res_exec["status"]["exit_code"], 0)
                    self.assertEqual(len(fake_worker.ssh_exec_calls), 1)
                    self.assertIn("CERTDATA", fake_worker.ssh_exec_calls[0]["certificate"])

                    res_exec_local = await exec_tools.manage_terminal(
                        action="exec",
                        command="pwd",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_exec_local["status"]["status"], "success")
                    self.assertEqual(res_exec_local["status"]["mode"], "local")
                    self.assertEqual(res_exec_local["status"]["target"], "local:/sandbox")
                    self.assertEqual(res_exec_local["status"]["exit_code"], 0)
                    self.assertIn("local:pwd", res_exec_local["text"])
                    self.assertEqual(fake_worker.local_exec_calls[-1]["sub_sid"], "")

                    tok_sub = ECHO_SUBAGENT_CONTEXT.set({"is_subagent": True, "sub_sid": "dlg_sub_1"})
                    try:
                        res_exec_sub = await exec_tools.manage_terminal(
                            action="exec",
                            mode="local",
                            command="ls",
                            __user__={"id": uid},
                            __metadata__={"chat_id": cid},
                        )
                    finally:
                        ECHO_SUBAGENT_CONTEXT.reset(tok_sub)
                    self.assertEqual(res_exec_sub["status"]["status"], "success")
                    self.assertEqual(res_exec_sub["status"]["target"], "local:/sandbox/dlg_sub_1")
                    self.assertEqual(fake_worker.local_exec_calls[-1]["sub_sid"], "dlg_sub_1")

                    res_exec_timeout = await exec_tools.manage_terminal(
                        action="exec",
                        mode="local",
                        command="SLEEP_TIMEOUT",
                        timeout_sec=5,
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_exec_timeout["status"]["status"], "error")
                    self.assertEqual(res_exec_timeout["status"]["error_code"], "execution_timeout")
                    self.assertIn("Timeout", res_exec_timeout["error"])

                    res_bad_local_acc = await exec_tools.manage_terminal(
                        action="exec",
                        mode="local",
                        account_id="vps-prod",
                        command="id",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_bad_local_acc["status"]["status"], "error")
                    self.assertEqual(res_bad_local_acc["status"]["error_code"], "invalid_arguments")
                    self.assertIn("interdit", res_bad_local_acc["error"])

                    res_bad_ssh_no_acc = await exec_tools.manage_terminal(
                        action="exec",
                        mode="ssh",
                        command="id",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_bad_ssh_no_acc["status"]["status"], "error")
                    self.assertEqual(res_bad_ssh_no_acc["status"]["error_code"], "invalid_arguments")
                    self.assertIn("list_identities(service='ssh')", res_bad_ssh_no_acc["text"])

                    res_missing_vault = await exec_tools.manage_terminal(
                        action="exec",
                        mode="ssh",
                        account_id="absent-vps",
                        command="id",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_missing_vault["status"]["status"], "error")
                    self.assertEqual(res_missing_vault["status"]["error_code"], "vault_account_not_found")
                    self.assertIn("Coffre & Identité", res_missing_vault["text"])

                    # 2. Garde headless_forbidden sur action="open" en automatisation
                    tok_auto = ECHO_AUTOMATION_CONTEXT.set({"is_automation": True, "automation_id": "auto-1"})
                    try:
                        res_headless = await exec_tools.manage_terminal(
                            action="open", mode="local", __user__={"id": uid}, __metadata__={"chat_id": cid}
                        )
                    finally:
                        ECHO_AUTOMATION_CONTEXT.reset(tok_auto)
                    self.assertEqual(res_headless["status"]["error_code"], "headless_forbidden")

                    # 3. open local déverrouillé + diffusion WM (nom par défaut 'Terminal')
                    res_open_local = await exec_tools.manage_terminal(
                        action="open",
                        mode="local",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid, "message_id": "msg-1"},
                    )
                    self.assertEqual(res_open_local["status"]["status"], "success")
                    local_sess = res_open_local["status"]["session"]
                    self.assertEqual(local_sess["name"], "Terminal")
                    self.assertFalse(local_sess["agent_locked"])
                    self.assertEqual(fake_wm.opened_tabs[-1][2], "terminal")
                    self.assertFalse(fake_wm.opened_tabs[-1][3]["agentLocked"])
                    self.assertEqual(fake_wm.terminal_states[-1][3], "opened")

                    # 4. open SSH verrouillé par défaut
                    res_open_ssh = await exec_tools.manage_terminal(
                        action="open",
                        mode="ssh",
                        account_id="vps-prod",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid, "message_id": "msg-2"},
                    )
                    self.assertEqual(res_open_ssh["status"]["status"], "success")
                    ssh_sess = res_open_ssh["status"]["session"]
                    ssh_sid = ssh_sess["id"]
                    self.assertTrue(ssh_sess["agent_locked"])
                    self.assertIn("verrouillée en lecture seule par défaut", res_open_ssh["text"])

                    # 5. list + read autorisé sous verrou
                    res_list = await exec_tools.manage_terminal(
                        action="list", __user__={"id": uid}, __metadata__={"chat_id": cid}
                    )
                    self.assertEqual(res_list["status"]["count"], 2)

                    res_read = await exec_tools.manage_terminal(
                        action="read", session_id=ssh_sid, __user__={"id": uid}, __metadata__={"chat_id": cid}
                    )
                    self.assertEqual(res_read["status"]["status"], "success")
                    self.assertIn("output:vps-prod", res_read["text"])

                    # 6. write et close bloqués sous verrou (423 -> terminal_locked + PAS exact)
                    expected_pas = echo_terminals.format_terminal_locked_message("vps-prod", ssh_sid)
                    res_write_locked = await exec_tools.manage_terminal(
                        action="write",
                        session_id=ssh_sid,
                        command="ls",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_write_locked["status"]["error_code"], "terminal_locked")
                    self.assertEqual(res_write_locked["text"], expected_pas)

                    res_close_locked = await exec_tools.manage_terminal(
                        action="close",
                        session_id=ssh_sid,
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_close_locked["status"]["error_code"], "terminal_locked")
                    self.assertEqual(res_close_locked["text"], expected_pas)

                    # 7. query_registry(resource_type="terminal") et détail par resource_id
                    reg_list = await reg_tools.query_registry(
                        resource_type="terminal", __user__={"id": uid}, __metadata__={"chat_id": cid}
                    )
                    reg_list_text = reg_list["text"] if isinstance(reg_list, dict) else reg_list
                    self.assertIn(ssh_sid, reg_list_text)
                    self.assertIn("active_locked", reg_list_text)

                    reg_detail = await reg_tools.query_registry(
                        resource_id=ssh_sid, __user__={"id": uid}, __metadata__={"chat_id": cid}
                    )
                    reg_detail_text = reg_detail["text"] if isinstance(reg_detail, dict) else reg_detail
                    self.assertIn("🔒 Verrouillé (lecture seule)", reg_detail_text)
                    self.assertIn("ubuntu@192.168.1.50:2222", reg_detail_text)

                    # 8. Déverrouillage + write + close
                    unlock_res = await echo_terminals.worker_set_terminal_lock(uid, cid, ssh_sid, False)
                    self.assertEqual(unlock_res["status"], "success")
                    res_write_ok = await exec_tools.manage_terminal(
                        action="write",
                        session_id=ssh_sid,
                        command="uptime",
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_write_ok["status"]["status"], "success")
                    res_close_ok = await exec_tools.manage_terminal(
                        action="close",
                        session_id=ssh_sid,
                        __user__={"id": uid},
                        __metadata__={"chat_id": cid},
                    )
                    self.assertEqual(res_close_ok["status"]["status"], "terminated")
                    self.assertEqual(fake_wm.terminal_states[-1][3], "closed")

                    # 9. Garde anti-BDD fantôme sur reconcile_chat_terminals_registry
                    ghost_db = echo_paths.get_echo_session_path(uid, ghost_cid, "db")
                    self.assertFalse(os.path.exists(ghost_db))
                    ghost_items = await echo_terminals.reconcile_chat_terminals_registry(uid, ghost_cid)
                    self.assertEqual(ghost_items, [])
                    self.assertFalse(os.path.exists(ghost_db))

                    # 10. Purge 'terminal' dans echo_session_clone.purge_conversational_state
                    with closing(echo_db_schema.open_db(echo_paths.get_echo_session_path(uid, cid, "db"))) as conn:
                        cur = conn.cursor()
                        before_cnt = cur.execute(
                            "SELECT COUNT(*) FROM echo_resources WHERE resource_type = 'terminal'"
                        ).fetchone()[0]
                        self.assertGreaterEqual(before_cnt, 2)
                        echo_session_clone.purge_conversational_state(cur)
                        conn.commit()
                        after_cnt = cur.execute(
                            "SELECT COUNT(*) FROM echo_resources WHERE resource_type = 'terminal'"
                        ).fetchone()[0]
                        self.assertEqual(after_cnt, 0)
                finally:
                    echo_crypto._SCOPED_KEY_CACHE.clear()

    async def test_wrap_tool_output_error_root_and_unbox_dedup(self) -> None:
        out_err = echo_core.wrap_tool_output(
            text="Paramètre invalide",
            status={"status": "error", "error_code": "invalid_arguments"},
        )
        self.assertEqual(out_err["error"], "Paramètre invalide")

        out_failed = echo_core.wrap_tool_output(
            text="Ignored",
            status={"status": "failed", "error": "Échec critique"},
        )
        self.assertEqual(out_failed["error"], "Échec critique")

        out_ok = echo_core.wrap_tool_output(text="OK", status={"status": "success"})
        self.assertNotIn("error", out_ok)

        finalized = echo_core._finalize_tool_result({"text": "Brut failed", "status": {"status": "failed"}})
        self.assertEqual(finalized["error"], "Brut failed")

        # unbox_tool_output sans 'text' ne doit pas ré-sérialiser 'error' dans result
        unboxed_no_text = echo_core.unbox_tool_output(
            "dummy_tool",
            {"text": "", "status": {"status": "error"}, "error": "Dup", "extra": 42},
            "MODEL_APOLLO",
        )
        resp_no_text = unboxed_no_text[0]["functionResponse"]["response"]
        self.assertEqual(json.loads(resp_no_text["result"]), {"extra": 42})

        # unbox_tool_output avec 'text' ne duplique pas la clé racine 'error'
        unboxed_with_text = echo_core.unbox_tool_output("dummy_tool", out_err, "MODEL_APOLLO")
        resp_with_text = unboxed_with_text[0]["functionResponse"]["response"]
        self.assertEqual(resp_with_text["result"], "Paramètre invalide")
        self.assertNotIn("error", resp_with_text)

        # StandardToolDispatcher._invoke_single_tool détecte l'erreur puis supprime le doublon racine 'error'
        async def _err_tool(**kwargs):
            return echo_core.wrap_tool_output(
                text="Boom tool",
                status={"status": "error", "error_code": "invalid_arguments"},
            )

        dispatcher = echo_subagent_hub.StandardToolDispatcher(
            sid="dlg_test",
            chat_id="c_test",
            user_id="u_test",
            state=None,
            sub_tools={"dummy_err_tool": {"callable": _err_tool}},
            body_tools_specs=None,
            events=None,
        )
        fr_part, is_err, _ = await dispatcher._invoke_single_tool("dummy_err_tool", {}, "call-1")
        self.assertTrue(is_err)
        self.assertNotIn("error", fr_part["functionResponse"]["response"])
        self.assertEqual(fr_part["functionResponse"]["response"]["text"], "Boom tool")


class TestN8NOrchestratorE2E(unittest.IsolatedAsyncioTestCase):
    """(d) E2E complet n8n_orchestrator_tool sur vraies SQLite + AEAD réelle + mock httpx."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.users_root = os.path.join(self._tmp.name, "users")
        os.makedirs(self.users_root, exist_ok=True)
        self.uid = "user_n8n"
        self.cid = "chat_n8n"
        self.user = {"id": self.uid}
        self.meta = {"chat_id": self.cid}
        self.secret_val = "sk-live-secret-token-987654"

        self._patches = [
            mock.patch.dict(os.environ, {"ECHO_VAULT_ENCRYPTION_KEY": "test-zone-a-master-key-32bytes!!"}),
            mock.patch.object(echo_constants, "ECHO_USERS_ROOT", self.users_root),
            mock.patch.object(echo_paths, "ECHO_USERS_ROOT", self.users_root),
            mock.patch.object(echo_state_manager, "ECHO_USERS_ROOT", self.users_root),
        ]
        for p in self._patches:
            p.start()
        echo_crypto._SCOPED_KEY_CACHE.clear()

        echo_db_schema.ensure_db(echo_paths.get_echo_identity_db_path(self.uid), echo_db_schema.DB_KIND_IDENTITY)
        echo_db_schema.ensure_db(echo_paths.get_echo_session_db_path(self.uid, self.cid), echo_db_schema.DB_KIND_SESSION)

        self.id_state = echo_state_manager.EchoStateManager(user_id=self.uid)
        self.sess_state = echo_state_manager.EchoStateManager(user_id=self.uid, chat_id=self.cid)
        self.id_state.save_vault_credential("n8n_workflows", "MY_API_KEY", self.secret_val)
        self.tools = n8n_orch_mod.Tools()

    def tearDown(self) -> None:
        echo_crypto._SCOPED_KEY_CACHE.clear()
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    async def test_templates_hub_workflows_and_ssot_grapher(self) -> None:
        valid_wf = {
            "name": "Sync Weather",
            "nodes": [
                {
                    "name": "HTTP Request",
                    "type": "n8n-nodes-base.httpRequest",
                    "parameters": {
                        "url": "https://example.com",
                        "headerParametersUi": {
                            "parameter": [{"name": "Authorization", "value": "Bearer __ECHO_SECRET_MY_API_KEY__"}]
                        },
                    },
                }
            ],
        }
        valid_wf_str = json.dumps(valid_wf)

        # 1. Templates CRUD + PAS
        res_bad_tpl = await self.tools.manage_n8n_template(
            action="delete", template_id="tpl1", content="{}", __user__=self.user, __metadata__=self.meta
        )
        self.assertEqual(res_bad_tpl["status"]["status"], "error")

        res_c = await self.tools.manage_n8n_template(
            action="create", template_id="tpl1", content=valid_wf_str, __user__=self.user, __metadata__=self.meta
        )
        self.assertEqual(res_c["status"]["status"], "success")
        self.assertIsNotNone(self.id_state.get_resource("tpl1"))

        updated_wf = dict(valid_wf, name="Sync Weather V2")
        res_u = await self.tools.manage_n8n_template(
            action="update", template_id="tpl1", content=json.dumps(updated_wf), __user__=self.user, __metadata__=self.meta
        )
        self.assertEqual(res_u["status"]["status"], "success")
        self.assertEqual(self.id_state.get_resource("tpl1")["name"], "Sync Weather V2")

        # 2. Hub search & download (avec credentials natifs -> avertissement)
        hub_requests = []
        worker_requests = []

        class _FakeAsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                hub_requests.append(url)
                if "templates/search" in url:
                    return types.SimpleNamespace(
                        status_code=200,
                        raise_for_status=lambda: None,
                        json=lambda: {"workflows": [{"id": 99, "name": "Hub Flow", "description": "Desc"}]},
                    )
                if "workflows/templates/99" in url:
                    return types.SimpleNamespace(
                        status_code=200,
                        raise_for_status=lambda: None,
                        json=lambda: {
                            "name": "Hub Flow",
                            "workflow": {"name": "Hub Flow", "nodes": [{"name": "N1", "credentials": {"http": "x"}}]},
                        },
                    )
                return types.SimpleNamespace(status_code=404, raise_for_status=lambda: None, json=lambda: {})

            async def post(self, url, json=None):
                worker_requests.append(("POST", url, json))
                if url.endswith("/execute"):
                    wf_sent = json["workflow_json"]
                    if not json["sync"]:
                        return types.SimpleNamespace(
                            status_code=200, json=lambda: {"status": "running", "execution_id": "exec-77"}
                        )
                    if "BIG_PAYLOAD" in wf_sent:
                        return types.SimpleNamespace(
                            status_code=200, json=lambda: {"status": "success", "stdout": "X" * 9000, "stderr": ""}
                        )
                    return types.SimpleNamespace(
                        status_code=200,
                        json=lambda: {"status": "success", "stdout": f"OK token={self.secret_val}", "stderr": ""},
                    )
                if url.endswith("/deploy"):
                    deploy_idx = sum(1 for m, u, _ in worker_requests if m == "POST" and u.endswith("/deploy"))
                    return types.SimpleNamespace(
                        status_code=200, json=lambda: {"status": "success", "n8n_id": f"n8n_{deploy_idx}"}
                    )
                return types.SimpleNamespace(status_code=404, text="not found")

            async def delete(self, url, params=None):
                worker_requests.append(("DELETE", url, params))
                return types.SimpleNamespace(status_code=200, json=lambda: {"killed_processes": 1})

        # bind secret_val for closure inside _FakeAsyncClient
        _FakeAsyncClient.secret_val = self.secret_val

        with mock.patch("httpx.AsyncClient", _FakeAsyncClient):
            res_hub_s = await self.tools.n8n_hub(
                action="search", query="weather", __user__=self.user, __metadata__=self.meta
            )
            self.assertIn("ID: 99", res_hub_s["text"])

            res_hub_d = await self.tools.n8n_hub(
                action="download", hub_id="99", template_id="tpl_hub", __user__=self.user, __metadata__=self.meta
            )
            self.assertIn("[Avertissement Architectural]", res_hub_d["text"])
            self.assertIsNotNone(self.id_state.get_resource("tpl_hub"))

            # 3. Workflow PAS guards & missing secret message (`ask_user_input`)
            bad_prep_both = await self.tools.manage_n8n_workflow(
                action="prepare",
                content=valid_wf_str,
                from_template_id="tpl1",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(bad_prep_both["status"]["status"], "error")

            bad_prep_id = await self.tools.manage_n8n_workflow(
                action="prepare", n8n_workflow_id="wf-x", content=valid_wf_str, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(bad_prep_id["status"]["status"], "error")

            missing_sec_wf = valid_wf_str.replace("__ECHO_SECRET_MY_API_KEY__", "__ECHO_SECRET_ABSENT_KEY__")
            res_missing = await self.tools.manage_n8n_workflow(
                action="prepare", content=missing_sec_wf, __user__=self.user, __metadata__=self.meta
            )
            self.assertIn("ask_user_input", res_missing["text"])

            # 4. Prepare from template + update + run_oneshot sync (AEAD injection + masking)
            res_prep = await self.tools.manage_n8n_workflow(
                action="prepare", from_template_id="tpl1", __user__=self.user, __metadata__=self.meta
            )
            wf_id = res_prep["text"].split("n8n_workflow_id=")[1].strip()
            self.assertEqual(self.sess_state.get_resource(wf_id)["status"], "ready")

            res_sync = await self.tools.manage_n8n_workflow(
                action="run_oneshot", n8n_workflow_id=wf_id, sync=True, __user__=self.user, __metadata__=self.meta
            )
            self.assertNotIn(self.secret_val, res_sync["text"])
            self.assertIn("[MASQUÉ:__ECHO_SECRET_MY_API_KEY__]", res_sync["text"])
            last_exec_payload = worker_requests[-1][2]
            self.assertIn(self.secret_val, last_exec_payload["workflow_json"])

            # 5. run_oneshot > 8Ko bloqué via override + async OK
            res_8k = await self.tools.manage_n8n_workflow(
                action="run_oneshot",
                n8n_workflow_id=wf_id,
                sync=True,
                parameters_override={"HTTP Request": {"url": "https://example.com/BIG_PAYLOAD"}},
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertIn("Payload excessif (>8Ko)", res_8k["text"])

            res_async = await self.tools.manage_n8n_workflow(
                action="run_oneshot", n8n_workflow_id=wf_id, sync=False, __user__=self.user, __metadata__=self.meta
            )
            self.assertIn("exec-77", res_async["text"])

            # 6. deploy_daemon x2 -> dépublication ancien démon n8n_1
            res_dep1 = await self.tools.manage_n8n_workflow(
                action="deploy_daemon", n8n_workflow_id=wf_id, __user__=self.user, __metadata__=self.meta
            )
            self.assertIn("n8n_1", res_dep1["text"])
            self.assertEqual(self.sess_state.get_resource(wf_id)["status"], "deployed_as_n8n_1")

            res_dep2 = await self.tools.manage_n8n_workflow(
                action="deploy_daemon", n8n_workflow_id=wf_id, __user__=self.user, __metadata__=self.meta
            )
            self.assertIn("n8n_2", res_dep2["text"])
            self.assertTrue(
                any(m == "DELETE" and u.endswith("/workflow/n8n_1") for m, u, _ in worker_requests)
            )

            # 7. delete purge fichiers générés + démon + BDD
            gen_dir = Path(self._tmp.name) / "generated_bin"
            gen_dir.mkdir(exist_ok=True)
            gen_file = gen_dir / f"out_{wf_id}_report.csv"
            gen_file.write_text("col1\nval1\n", encoding="utf-8")
            files_dir = Path(echo_paths.get_echo_session_path(self.uid, self.cid, "files"))
            files_dir.mkdir(parents=True, exist_ok=True)
            (files_dir / gen_file.name).write_text("ref", encoding="utf-8")
            self.sess_state.save_resource(
                id="bin-1",
                name=gen_file.name,
                resource_type="binary",
                status="put_in_context",
                storage_path=str(gen_file),
            )

            res_del_wf = await self.tools.manage_n8n_workflow(
                action="delete", n8n_workflow_id=wf_id, __user__=self.user, __metadata__=self.meta
            )
            self.assertIn("1 fichiers générés par ce workflow ont été purgés", res_del_wf["text"])
            self.assertFalse(gen_file.exists())
            self.assertFalse((files_dir / gen_file.name).exists())
            self.assertIsNone(self.sess_state.get_resource(wf_id))
            self.assertTrue(
                any(m == "DELETE" and u.endswith("/workflow/n8n_2") for m, u, _ in worker_requests)
            )

            # Delete template
            res_del_tpl = await self.tools.manage_n8n_template(
                action="delete", template_id="tpl1", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_del_tpl["status"]["status"], "success")
            self.assertIsNone(self.id_state.get_resource("tpl1"))

    async def test_delegate_to_n8n_grapher_uses_ssot_allowed_tools(self) -> None:
        captured = {}

        async def _fake_run_or_resume(**kwargs):
            captured.update(kwargs)
            return {"text": "Graphe prêt.", "status": {"status": "success"}}

        with mock.patch.object(n8n_orch_mod.EchoSubagentController, "run_or_resume", side_effect=_fake_run_or_resume):
            res = await self.tools.delegate_to_n8n_grapher(
                instructions="Créer un workflow", __user__=self.user, __metadata__=self.meta
            )

        self.assertIn("Rapport N8N Grapher", res["text"])
        self.assertEqual(
            captured["allowed_tools"],
            list(ECHO_SUBAGENT_ENGINES_REGISTRY["n8n_grapher"]["default_allowed_tools"]),
        )

    async def test_n8n_api_stop_and_delete_workflow(self) -> None:
        proc1 = types.SimpleNamespace(pid=1001, returncode=None, kill=mock.Mock())
        proc2 = types.SimpleNamespace(pid=1002, returncode=None, kill=mock.Mock())
        proc3 = types.SimpleNamespace(pid=1003, returncode=None, kill=mock.Mock())

        mock_unpublish = mock.AsyncMock(return_value=True)
        mock_api_key = mock.AsyncMock(return_value="master-key")
        n8n_api_mod.active_executions.clear()
        n8n_api_mod.active_executions["wf_a"] = {"exec_1": proc1, "exec_2": proc2}
        n8n_api_mod.active_executions["wf_b"] = {"exec_3": proc3}

        try:
            with (
                mock.patch.object(n8n_api_mod, "_unpublish_and_delete_workflow", mock_unpublish),
                mock.patch.object(n8n_api_mod, "get_or_create_n8n_api_key", mock_api_key),
            ):
                # 1. Stop ciblé par execution_id seul sans n8n_workflow_id
                res1 = await n8n_api_mod.stop_workflow(
                    n8n_api_mod.StopRequest(user_id="u1", chat_id="c1", execution_id="exec_1")
                )
                self.assertEqual(res1["status"], "success")
                self.assertEqual(res1["killed_processes"], 1)
                self.assertEqual(res1["killed_execution_ids"], ["exec_1"])
                self.assertEqual(res1["resolved_workflow_id"], "wf_a")
                self.assertEqual(res1["remaining_active"], 1)
                self.assertFalse(res1["daemon_stopped"])
                proc1.kill.assert_called_once()
                proc2.kill.assert_not_called()
                mock_unpublish.assert_not_awaited()

                # 2. Stop workflow + daemon
                drop_dir = Path(self._tmp.name) / "n8n_drop_preserve"
                drop_dir.mkdir(exist_ok=True)
                wf_marker = drop_dir / "output.txt"
                wf_marker.write_text("keep", encoding="utf-8")

                res2 = await n8n_api_mod.stop_workflow(
                    n8n_api_mod.StopRequest(
                        user_id="u1", chat_id="c1", n8n_workflow_id="wf_a", daemon_n8n_id="n8n_daemon_1"
                    )
                )
                self.assertEqual(res2["status"], "success")
                self.assertEqual(res2["killed_processes"], 1)
                self.assertEqual(res2["killed_execution_ids"], ["exec_2"])
                self.assertTrue(res2["daemon_stopped"])
                self.assertEqual(res2["remaining_active"], 0)
                self.assertNotIn("wf_a", n8n_api_mod.active_executions)
                proc2.kill.assert_called_once()
                mock_unpublish.assert_awaited_once()
                self.assertEqual(mock_unpublish.call_args.args[2], "n8n_daemon_1")
                self.assertTrue(wf_marker.exists())

                # 3. DELETE /workflow/{id}
                res_del = await n8n_api_mod.delete_workflow("n8n_daemon_2", echo_workflow_id="wf_b")
                self.assertEqual(res_del["status"], "success")
                self.assertEqual(res_del["killed_processes"], 1)
                self.assertTrue(res_del["api_deleted"])
                proc3.kill.assert_called_once()
                self.assertNotIn("wf_b", n8n_api_mod.active_executions)

                # 4. Stop en pré-démarrage (proc is None avant import_proc)
                n8n_api_mod.active_executions["wf_boot"] = {"exec_boot": None}
                res_boot = await n8n_api_mod.stop_workflow(
                    n8n_api_mod.StopRequest(user_id="u1", chat_id="c1", execution_id="exec_boot")
                )
                self.assertEqual(res_boot["killed_processes"], 1)
                self.assertEqual(res_boot["killed_execution_ids"], ["exec_boot"])
                self.assertEqual(res_boot["resolved_workflow_id"], "wf_boot")
                self.assertIn("exec_boot", n8n_api_mod.cancelled_executions)
                n8n_api_mod.cancelled_executions.discard("exec_boot")

                # 5. Stop sur processus déjà terminé (returncode = 0) -> ne pas marquer annulé
                proc_done = types.SimpleNamespace(pid=1004, returncode=0, kill=mock.Mock())
                n8n_api_mod.active_executions["wf_done"] = {"exec_done": proc_done}
                res_done = await n8n_api_mod.stop_workflow(
                    n8n_api_mod.StopRequest(user_id="u1", chat_id="c1", execution_id="exec_done")
                )
                self.assertEqual(res_done["killed_processes"], 0)
                self.assertNotIn("exec_done", n8n_api_mod.cancelled_executions)
                proc_done.kill.assert_not_called()

                # 6. _run_n8n_process annulé avant démarrage -> "cancelled", pas de rapport, purge finally
                cancel_target = Path(self._tmp.name) / "n8n_cancel_target"
                cancel_target.mkdir(parents=True, exist_ok=True)
                cancel_tmp = Path(self._tmp.name) / "cancel_wf.json"
                cancel_tmp.write_text("{}", encoding="utf-8")
                n8n_api_mod.cancelled_executions.add("exec_pre_cancel")
                req_cancel = n8n_api_mod.ExecuteRequest(
                    user_id="u1",
                    chat_id="c1",
                    n8n_workflow_id="wf_cancel",
                    workflow_json="{}",
                    sync=False,
                )
                st_cancel, out_cancel, err_cancel = await n8n_api_mod._run_n8n_process(
                    req_cancel, cancel_target, cancel_tmp, Path(self._tmp.name), execution_id="exec_pre_cancel"
                )
                self.assertEqual(st_cancel, "cancelled")
                self.assertEqual(out_cancel, "")
                self.assertEqual(err_cancel, "")
                self.assertFalse((cancel_target / "execution_report.json").exists())
                self.assertNotIn("exec_pre_cancel", n8n_api_mod.cancelled_executions)
        finally:
            n8n_api_mod.active_executions.clear()
            n8n_api_mod.cancelled_executions.clear()

    async def test_n8n_stop_and_nine_error_warning_cases(self) -> None:
        valid_wf = {
            "name": "Stop & Error Flow",
            "nodes": [
                {
                    "name": "HTTP Request",
                    "type": "n8n-nodes-base.httpRequest",
                    "parameters": {
                        "url": "https://example.com",
                        "headerParametersUi": {
                            "parameter": [{"name": "Authorization", "value": "Bearer __ECHO_SECRET_MY_API_KEY__"}]
                        },
                    },
                }
            ],
        }
        valid_wf_str = json.dumps(valid_wf)

        res_prep = await self.tools.manage_n8n_workflow(
            action="prepare", content=valid_wf_str, __user__=self.user, __metadata__=self.meta
        )
        wf_id = res_prep["text"].split("n8n_workflow_id=")[1].strip()
        wf_path = self.tools._get_workflow_path(self.uid, self.cid, wf_id)

        # Rejet execution_id hors-contrat sur les autres actions
        for act in ("prepare", "update", "delete", "run_oneshot", "deploy_daemon"):
            bad_arg = await self.tools.manage_n8n_workflow(
                action=act,
                n8n_workflow_id=None if act == "prepare" else wf_id,
                content=valid_wf_str if act in ("prepare", "update") else None,
                execution_id="exec-77",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(bad_arg["status"]["status"], "error", f"action={act} devrait rejeter execution_id")
            self.assertEqual(bad_arg["status"]["error_code"], "invalid_arguments")

        stop_payloads = []
        mode = {"execute": "error", "deploy": "warning", "stop": "active"}

        class _MockClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                if "templates/404" in url:
                    return types.SimpleNamespace(status_code=404, raise_for_status=lambda: None, json=lambda: {})
                if "templates/bad" in url:
                    return types.SimpleNamespace(
                        status_code=200,
                        raise_for_status=lambda: None,
                        json=lambda: {"name": "Bad", "workflow": {"name": "Bad"}},
                    )
                if "templates/dup" in url:
                    return types.SimpleNamespace(
                        status_code=200,
                        raise_for_status=lambda: None,
                        json=lambda: {"name": "Dup", "workflow": {"name": "Dup", "nodes": []}},
                    )
                return types.SimpleNamespace(status_code=404, raise_for_status=lambda: None, json=lambda: {})

            async def post(self, url, json=None):
                if url.endswith("/stop"):
                    stop_payloads.append(json)
                    if mode["stop"] == "noop":
                        return types.SimpleNamespace(
                            status_code=200,
                            json=lambda: {
                                "status": "success",
                                "killed_processes": 0,
                                "killed_execution_ids": [],
                                "daemon_stopped": False,
                                "resolved_workflow_id": json.get("n8n_workflow_id"),
                                "remaining_active": 0,
                            },
                        )
                    return types.SimpleNamespace(
                        status_code=200,
                        json=lambda: {
                            "status": "success",
                            "killed_processes": 1,
                            "killed_execution_ids": [json.get("execution_id") or "exec-77"],
                            "daemon_stopped": bool(json.get("daemon_n8n_id")),
                            "resolved_workflow_id": wf_id,
                            "remaining_active": 0,
                        },
                    )
                if url.endswith("/execute"):
                    if mode["execute"] == "big":
                        return types.SimpleNamespace(
                            status_code=200, json=lambda: {"status": "success", "stdout": "Z" * 9000, "stderr": ""}
                        )
                    if mode["execute"] == "big_error":
                        return types.SimpleNamespace(
                            status_code=200,
                            json=lambda: {
                                "status": "error",
                                "stdout": "",
                                "stderr": "START_ERR " + ("A" * 9500) + " boom_test_6242",
                            },
                        )
                    return types.SimpleNamespace(
                        status_code=200, json=lambda: {"status": "error", "stdout": "", "stderr": "Node failed"}
                    )
                if url.endswith("/deploy"):
                    return types.SimpleNamespace(
                        status_code=200,
                        json=lambda: {"status": "warning", "n8n_id": "n8n_warn_1", "detail": "Activation différée"},
                    )
                return types.SimpleNamespace(status_code=500, text="err")

        with mock.patch("httpx.AsyncClient", _MockClient):
            # Test action="stop" via execution_id
            self.sess_state.update_resource_status(wf_id, "executing")
            res_stop_eid = await self.tools.manage_n8n_workflow(
                action="stop", execution_id="exec-77", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_stop_eid["status"]["status"], "success")
            self.assertTrue(wf_path.exists())
            self.assertEqual(self.sess_state.get_resource(wf_id)["status"], "ready")
            self.assertEqual(stop_payloads[-1]["execution_id"], "exec-77")

            # Test action="stop" sur démon déployé
            self.sess_state.update_resource_status(wf_id, "deployed_as_n8n_warn_1")
            res_stop_wid = await self.tools.manage_n8n_workflow(
                action="stop", n8n_workflow_id=wf_id, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_stop_wid["status"]["status"], "success")
            self.assertTrue(res_stop_wid["status"]["daemon_stopped"])
            self.assertEqual(stop_payloads[-1]["daemon_n8n_id"], "n8n_warn_1")
            self.assertTrue(wf_path.exists())
            self.assertEqual(self.sess_state.get_resource(wf_id)["status"], "ready")

            # Cas 1 : deploy_daemon ID inexistant -> "error"
            res_c1 = await self.tools.manage_n8n_workflow(
                action="deploy_daemon", n8n_workflow_id="inexistant-id", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c1["status"]["status"], "error")
            self.assertEqual(res_c1["status"]["error_code"], "not_found")

            # Cas 2 : run_oneshot(sync=True) worker status="error" -> "error" + SQLite "error"
            mode["execute"] = "error"
            res_c2 = await self.tools.manage_n8n_workflow(
                action="run_oneshot", n8n_workflow_id=wf_id, sync=True, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c2["status"]["status"], "error")
            self.assertEqual(res_c2["status"]["error_code"], "execution_failed")
            self.assertEqual(self.sess_state.get_resource(wf_id)["status"], "error")

            # Cas 3 : run_oneshot(sync=True) payload > 8 Ko -> "error"
            mode["execute"] = "big"
            res_c3 = await self.tools.manage_n8n_workflow(
                action="run_oneshot", n8n_workflow_id=wf_id, sync=True, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c3["status"]["status"], "error")
            self.assertEqual(res_c3["status"]["error_code"], "payload_too_large")

            # Cas 3bis : run_oneshot(sync=True) erreur > 8 Ko -> execution_failed + troncature Head+Tail < 8Ko
            mode["execute"] = "big_error"
            res_c3b = await self.tools.manage_n8n_workflow(
                action="run_oneshot", n8n_workflow_id=wf_id, sync=True, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c3b["status"]["status"], "error")
            self.assertEqual(res_c3b["status"]["error_code"], "execution_failed")
            self.assertEqual(self.sess_state.get_resource(wf_id)["status"], "error")
            self.assertIn("START_ERR", res_c3b["text"])
            self.assertIn("boom_test_6242", res_c3b["text"])
            self.assertLess(len(res_c3b["text"].encode("utf-8")), 8192)

            # Cas 4 : violation _check_secrets / _resolve_workflow_secrets -> "error"
            bad_sec_wf = valid_wf_str.replace("__ECHO_SECRET_MY_API_KEY__", "__ECHO_SECRET_MISSING_KEY__")
            res_c4 = await self.tools.manage_n8n_workflow(
                action="prepare", content=bad_sec_wf, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c4["status"]["status"], "error")
            self.assertEqual(res_c4["status"]["error_code"], "security_violation")

            # Cas 5 : deploy_daemon worker status="warning" -> "warning"
            res_c5 = await self.tools.manage_n8n_workflow(
                action="deploy_daemon", n8n_workflow_id=wf_id, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c5["status"]["status"], "warning")

            # Cas 6 : n8n_hub(action="download") 404 -> "error"
            res_c6 = await self.tools.n8n_hub(
                action="download", hub_id="404", template_id="t404", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c6["status"]["status"], "error")
            self.assertEqual(res_c6["status"]["error_code"], "not_found")

            # Cas 7 : n8n_hub(action="download") JSON invalide sans nodes -> "error"
            res_c7 = await self.tools.n8n_hub(
                action="download", hub_id="bad", template_id="tbad", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c7["status"]["status"], "error")
            self.assertEqual(res_c7["status"]["error_code"], "invalid_hub_payload")

            # Cas 8 : n8n_hub(action="download") sur template_id déjà existant -> "error"
            await self.tools.manage_n8n_template(
                action="create", template_id="tdup", content=valid_wf_str, __user__=self.user, __metadata__=self.meta
            )
            res_c8 = await self.tools.n8n_hub(
                action="download", hub_id="dup", template_id="tdup", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c8["status"]["status"], "error")
            self.assertEqual(res_c8["status"]["error_code"], "already_exists")

            # Cas 9 : manage_n8n_template ID inexistant / doublon -> "error"
            res_c9_dup = await self.tools.manage_n8n_template(
                action="create", template_id="tdup", content=valid_wf_str, __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c9_dup["status"]["status"], "error")
            self.assertEqual(res_c9_dup["status"]["error_code"], "already_exists")

            res_c9_miss = await self.tools.manage_n8n_template(
                action="delete", template_id="missing_tpl", __user__=self.user, __metadata__=self.meta
            )
            self.assertEqual(res_c9_miss["status"]["status"], "error")
            self.assertEqual(res_c9_miss["status"]["error_code"], "not_found")


class TestUniversalVisualGeneratorGuards(unittest.IsolatedAsyncioTestCase):
    """Vérification des gardes de `close_image_session` (headless & arguments)."""

    async def test_close_image_session_guards(self) -> None:
        vtools = visual_gen_mod.Tools()
        user = {"id": "user-vis"}
        meta = {"chat_id": "chat-vis"}

        tok = ECHO_SUBAGENT_CONTEXT.set({"is_subagent": True, "sub_sid": "dlg_parent"})
        try:
            res_hl = await vtools.close_image_session(sub_sid="dlg_img_1", __user__=user, __metadata__=meta)
        finally:
            ECHO_SUBAGENT_CONTEXT.reset(tok)
        self.assertEqual(res_hl["status"]["status"], "error")
        self.assertEqual(res_hl["status"]["error_code"], "headless_forbidden")

        res_empty = await vtools.close_image_session(sub_sid="   ", __user__=user, __metadata__=meta)
        self.assertEqual(res_empty["status"]["status"], "error")
        self.assertEqual(res_empty["status"]["error_code"], "invalid_arguments")

        res_no_chat = await vtools.close_image_session(sub_sid="dlg_img_1", __user__=user, __metadata__={})
        self.assertEqual(res_no_chat["status"]["status"], "error")
        self.assertEqual(res_no_chat["status"]["error_code"], "missing_chat_id")


class TestToolGuidesAndAdvisor(unittest.IsolatedAsyncioTestCase):
    """(e) Tests EchoToolGuidesManager (_fold_accents, exclude_auto_domains) et tool_advisor."""

    def test_fold_accents_and_exclude_auto_domains(self) -> None:
        self.assertEqual(EchoToolGuidesManager._fold_accents("Événement créé à Noël"), "Evenement cree a Noel")
        self.assertTrue(EchoToolGuidesManager._match_term("creer une tache", "tâche"))
        self.assertTrue(EchoToolGuidesManager._match_term("Créer une tâche urgente", "tache"))
        self.assertFalse(EchoToolGuidesManager._match_term("la suite logique", "ui"))
        self.assertTrue(EchoToolGuidesManager._match_term("utiliser __echo_download_dir__/x", "__ECHO_DOWNLOAD_DIR__"))
        self.assertTrue(EchoToolGuidesManager._match_term("outil-web actif", "outil_web"))

        # Exclusion auto par mots-clés de echo_ui_guide, mais chargement explicite préservé
        res_excluded = EchoToolGuidesManager.resolve_guides(
            objective="Comment ouvrir la barre latérale ou les réglages de l'interface utilisateur ?",
            exclude_auto_domains={"echo_ui_guide"},
        )
        self.assertNotIn('domain_id="echo_ui_guide"', res_excluded)

        res_explicit_target = EchoToolGuidesManager.resolve_guides(
            objective="Aide interface",
            target_tools="echo_ui_guide",
            exclude_auto_domains={"echo_ui_guide"},
        )
        self.assertIn('domain_id="echo_ui_guide"', res_explicit_target)

        res_on_demand = EchoToolGuidesManager.format_guides_for_targets(["echo_ui_guide"])
        self.assertIn('domain_id="echo_ui_guide"', res_on_demand)

    async def test_tool_advisor_kv_cache_turn2_id_and_empty_response(self) -> None:
        gen_tools = generalist_mod.Tools()
        seen_payloads = []

        async def _fake_call_cascade(**kwargs):
            seen_payloads.append(copy.deepcopy(kwargs["payload"]))
            if len(seen_payloads) == 1:
                return (
                    {
                        "candidates": [
                            {
                                "content": {
                                    "role": "model",
                                    "parts": [
                                        {
                                            "functionCall": {
                                                "id": "fc-guide-1",
                                                "name": "read_tool_guide",
                                                "args": {"targets": ["echo_ui_guide"]},
                                            }
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                    "MODEL_APOLLO",
                    None,
                )
            return (
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "Réponse finale OK."}]}}]},
                "MODEL_APOLLO",
                None,
            )

        with (
            mock.patch.object(echo_subagent_hub, "get_chat_tools", return_value={}),
            mock.patch.object(generalist_mod.EchoToolGuidesManager, "resolve_guides", wraps=generalist_mod.EchoToolGuidesManager.resolve_guides) as spy_resolve,
            mock.patch.object(generalist_mod.EchoGeminiClient, "call_cascade", side_effect=_fake_call_cascade),
        ):
            tok = ECHO_SUBAGENT_CONTEXT.set({"is_subagent": True, "sub_sid": "dlg_sub"})
            try:
                res = await gen_tools.tool_advisor(
                    objective="Configurer les réglages interface",
                    __user__={"id": "u1"},
                    __metadata__={"chat_id": "c1"},
                )
            finally:
                ECHO_SUBAGENT_CONTEXT.reset(tok)

        self.assertEqual(spy_resolve.call_args.kwargs.get("exclude_auto_domains"), {"echo_ui_guide"})
        self.assertEqual(len(seen_payloads), 2)
        self.assertIn("tools", seen_payloads[0])
        self.assertNotIn("toolConfig", seen_payloads[0])
        self.assertEqual(seen_payloads[1]["tools"], seen_payloads[0]["tools"])
        self.assertEqual(seen_payloads[1]["toolConfig"], {"functionCallingConfig": {"mode": "NONE"}})
        fr_part = seen_payloads[1]["contents"][2]["parts"][0]["functionResponse"]
        self.assertEqual(fr_part["id"], "fc-guide-1")
        self.assertIn("Réponse finale OK.", res["text"])

        # Réponse vide / bloquée -> describe_empty_llm_response + status error
        async def _fake_blocked_cascade(**kwargs):
            return ({"promptFeedback": {"blockReason": "SAFETY"}}, "MODEL_APOLLO", None)

        with (
            mock.patch.object(echo_subagent_hub, "get_chat_tools", return_value={}),
            mock.patch.object(generalist_mod.EchoGeminiClient, "call_cascade", side_effect=_fake_blocked_cascade),
        ):
            res_empty = await gen_tools.tool_advisor(
                objective="Test blocage",
                __user__={"id": "u1"},
                __metadata__={"chat_id": "c1"},
            )
        self.assertEqual(res_empty["status"]["status"], "error")
        self.assertEqual(res_empty["status"]["error_code"], "prompt_blocked")
        self.assertEqual(res_empty["status"]["reason"], "SAFETY")


class TestDocGovernanceAndToolGuides(unittest.TestCase):
    """(f) Test permanent de gouvernance documentaire : bijection AST<->manuels, zéro résidu, anti-collision."""

    LEGACY_CONSOLIDATED_TOOLS = (
        "kill_sandbox_execution",
        "create_n8n_template",
        "modify_n8n_template",
        "delete_n8n_template",
        "prepare_n8n_workflow",
        "modify_n8n_workflow",
        "delete_n8n_workflow",
        "run_n8n_oneshot_workflow",
        "deploy_n8n_daemon",
        "search_n8n_hub",
        "download_n8n_hub_template",
        "search_ui_calendar_events",
        "list_ui_calendar_events",
        "read_ui_calendar_events",
        "create_ui_calendar_event",
        "update_ui_calendar_event",
        "delete_ui_calendar_event",
        "create_ui_automation",
        "update_ui_automation",
        "delete_ui_automation",
        "toggle_ui_automation",
        "read_ui_automations",
        "list_ui_automations",
        "create_ui_tasks",
        "update_ui_task",
        "ssh_executor",
    )

    def test_ast_to_tool_guides_bijection_and_no_duplicates(self) -> None:
        ast_tools: set[str] = set()
        for py_file in sorted(TOOLS_DIR.glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if any(isinstance(d, ast.Name) and d.id == "echo_tool" for d in node.decorator_list):
                        ast_tools.add(node.name)

        self.assertEqual(len(ast_tools), 87)

        guides = EchoToolGuidesManager.load_all_guides(force_reload=True)
        self.assertEqual(len(guides), 12)

        indexed_tools: list[str] = []
        for domain_id, info in guides.items():
            tools_list = info.get("tools", [])
            keywords_list = info.get("keywords", [])
            self.assertEqual(
                len(tools_list),
                len(set(tools_list)),
                f"Doublon intra-manuel dans `tools` de {domain_id}",
            )
            overlap = set(tools_list) & set(keywords_list)
            self.assertEqual(
                overlap,
                set(),
                f"Chevauchement `tools`/`keywords` interdit dans {domain_id}: {overlap}",
            )
            indexed_tools.extend(tools_list)

        self.assertEqual(
            len(indexed_tools),
            len(set(indexed_tools)),
            "Doublon inter-manuels détecté dans `tool_guides/*.md`",
        )
        self.assertEqual(set(indexed_tools) | {"tool_advisor"}, ast_tools)
        self.assertNotIn("tool_advisor", set(indexed_tools))

    def test_zero_legacy_consolidated_tool_mentions(self) -> None:
        legacy_pattern = re.compile(r"\b(" + "|".join(self.LEGACY_CONSOLIDATED_TOOLS) + r")\b")
        targets = [
            *sorted(TOOLS_DIR.glob("*.py")),
            *sorted(LIBS_DIR.glob("*.py")),
            *sorted((LIBS_DIR / "tool_guides").glob("*.md")),
            TOOLS_DIR / "AGENTS.md",
            LIBS_DIR / "AGENTS.md",
        ]
        offenders: list[tuple[str, list[str]]] = []
        for path in targets:
            text = path.read_text(encoding="utf-8")
            hits = sorted(set(legacy_pattern.findall(text)))
            if hits:
                offenders.append((str(path.relative_to(REPO_ROOT)), hits))

        self.assertEqual(offenders, [], f"Mentions résiduelles d'outils consolidés : {offenders}")

    def test_resolve_guides_accent_folding_and_no_ui_guide_collision(self) -> None:
        technical_queries = [
            ("Creer un evenement calendrier avec rappel a Noel", "ui_calendar_and_automations"),
            ("Planifier une tache cron recurrente", "ui_calendar_and_automations"),
            ("Deplacer un fichier codex vers un repertoire", "codex_management"),
            ("Executer un script python en sandbox et recuperer le resultat", "code_and_files"),
            ("Deployer un workflow n8n en daemon", "n8n_workflows"),
            ("Memoire long terme et recherche semantique", "memory_and_rag"),
            ("Rechercher des conversations passees", "ui_conversations"),
            ("Deleguer a une phalange ou une table ronde", "agent_orchestration"),
            ("Configurer une identite ou appeler un serveur mcp", "identity_and_api_mcp"),
            ("Naviguer sur une page web avec captures", "web_search_and_browsing"),
        ]
        for query, expected_domain in technical_queries:
            resolved = EchoToolGuidesManager.resolve_guides(
                objective=query,
                exclude_auto_domains={"echo_ui_guide"},
            )
            self.assertIn(f'domain_id="{expected_domain}"', resolved, f"Échec résolution pour: {query}")
            self.assertNotIn('domain_id="echo_ui_guide"', resolved, f"Collision echo_ui_guide sur: {query}")


if __name__ == "__main__":
    unittest.main()
