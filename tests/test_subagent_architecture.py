# -*- coding: utf-8 -*-
"""Tests unitaires et d'intégration pour l'architecture Sous-Agents V2, Cohorte, Phalange et Concile de Delphes V2."""

from __future__ import annotations

import asyncio
import copy
import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBS_DIR = REPO_ROOT / "14-owui-libs"
TOOLS_DIR = REPO_ROOT / "12-owui-tools"
if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))


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


import echo_browser_lib  # noqa: E402
import echo_constants  # noqa: E402
import echo_paths  # noqa: E402
import echo_prompts  # noqa: E402
import echo_skills  # noqa: E402
import echo_state_manager  # noqa: E402
import echo_subagent_hub  # noqa: E402
from echo_gemini_client import EchoGeminiClient, PromptBlockedError, describe_empty_llm_response  # noqa: E402
from echo_state_manager import EchoStateManager  # noqa: E402
from echo_subagent_hub import (  # noqa: E402
    EchoAgentRunner,
    EchoSubagentController,
    StandardToolDispatcher,
    _hard_delete_subagents_sync,
    compute_subagent_excluded_tools,
    extract_turn_origin,
    format_echo_message,
    get_chat_subagent_semaphore,
    get_subagent_meta,
    get_subagent_system_prompt,
    register_chat_tools,
    resolve_delegate_tools,
    resolve_target_sub_sids,
    resolve_updated_allowed_tools,
    save_subagent_meta,
    save_subagent_system_prompt,
    validate_or_create_sub_sid,
)

def _ensure_open_webui_stubs() -> None:
    """Fournit des stubs minimaux pour open_webui.* hors conteneur Docker."""
    if "open_webui.tools.builtin" in sys.modules:
        return
    try:
        import open_webui.tools.builtin  # type: ignore  # noqa: F401
        return
    except Exception:
        pass

    def _noop_fn(*args, **kwargs):
        return None

    class _TaskItem:
        pass

    class _FolderForm:
        pass

    mod_root = types.ModuleType("open_webui")
    mod_tools = types.ModuleType("open_webui.tools")
    mod_builtin = types.ModuleType("open_webui.tools.builtin")
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

    mod_models = types.ModuleType("open_webui.models")
    mod_folders = types.ModuleType("open_webui.models.folders")
    mod_folders.Folders = object
    mod_folders.FolderForm = _FolderForm
    mod_tags = types.ModuleType("open_webui.models.tags")
    mod_tags.Tags = object
    mod_chats = types.ModuleType("open_webui.models.chats")
    mod_chats.Chats = object
    mod_chats.is_internal_chat = lambda *args, **kwargs: False
    mod_auto = types.ModuleType("open_webui.models.automations")
    mod_auto.Automations = object
    mod_cal = types.ModuleType("open_webui.models.calendar")
    mod_cal.CalendarEvents = object
    mod_utils = types.ModuleType("open_webui.utils")
    mod_chat_id = types.ModuleType("open_webui.utils.chat_id")
    mod_chat_id.is_saved_chat_id = lambda *args, **kwargs: True

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


_ensure_open_webui_stubs()
agent_engine_mod = _load_tool_module("tool_agent_engine_tool", "agent_engine_tool.py")
agent_orch_mod = _load_tool_module("tool_agent_orchestration_tool", "agent_orchestration_tool.py")
data_broker_mod = _load_tool_module("tool_delegate_to_data_broker", "delegate_to_data_broker.py")
n8n_orch_mod = _load_tool_module("tool_n8n_orchestrator_tool", "n8n_orchestrator_tool.py")
web_search_mod = _load_tool_module("tool_sovereign_web_search", "sovereign_web_search.py")
generalist_mod = _load_tool_module("tool_generalist_tools", "generalist_tools.py")


def _make_tool_entry(name: str, fn=None, desc: str = "Outil de test"):
    if fn is None:
        async def _noop(**kwargs):
            return {"status": "success", "tool": name, "args": kwargs}
        fn = _noop
    return {
        "spec": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": {}},
        },
        "callable": fn,
    }


class _IsolatedEchoEnv:
    """Isole ECHO_USERS_ROOT et ECHO_SUBAGENT_CONTEXT dans un répertoire temporaire."""

    def __init__(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = self._tmpdir.name
        self._patches = [
            mock.patch.object(echo_constants, "ECHO_USERS_ROOT", self.root),
            mock.patch.object(echo_paths, "ECHO_USERS_ROOT", self.root),
            mock.patch.object(echo_state_manager, "ECHO_USERS_ROOT", self.root),
        ]

    def __enter__(self) -> str:
        for p in self._patches:
            p.start()
        return self.root

    def __exit__(self, exc_type, exc, tb) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmpdir.cleanup()


class TestSubagentArchitecture(unittest.IsolatedAsyncioTestCase):
    """Couvre les 7 blocs architecturaux des sous-agents, Phalange, Cohorte et Concile V2."""

    def setUp(self) -> None:
        self.user_id = "u_test_arch"
        self.user_dict = {"id": self.user_id}

    def tearDown(self) -> None:
        echo_subagent_hub._TOOLS_CACHE.clear()
        echo_subagent_hub._CHAT_SEMAPHORES.clear()

    # ------------------------------------------------------------------
    # Bloc 1 : Absence de cycle d'import & identité des ré-exports
    # ------------------------------------------------------------------
    def test_01_no_circular_import_and_browser_reexports(self) -> None:
        reloaded_browser = importlib.reload(echo_browser_lib)
        reloaded_hub = importlib.reload(echo_subagent_hub)
        expected_symbols = (
            "BrowserToolDispatcher",
            "deploy_browser_navigation_monitor",
            "format_browser_dom_view",
            "get_global_browser_semaphore",
            "prune_browser_heavy_context",
        )
        for sym in expected_symbols:
            self.assertTrue(hasattr(reloaded_browser, sym), f"Missing {sym} in echo_browser_lib")
            self.assertTrue(hasattr(reloaded_hub, sym), f"Missing {sym} in echo_subagent_hub")
            self.assertIs(
                getattr(reloaded_hub, sym),
                getattr(reloaded_browser, sym),
                f"Identity mismatch for re-exported symbol {sym}",
            )

    # ------------------------------------------------------------------
    # Bloc 2 : <ECHO_MESSAGE> idempotent + extract_turn_origin & rétrocompatibilité
    # ------------------------------------------------------------------
    def test_02_echo_message_format_idempotence_and_legacy_tags(self) -> None:
        # Idempotence sur les 4 origines
        cases = [
            ("Mission A", "orchestrator", "task", "orchestrator"),
            ("Réponse humain", "user", "human", "human"),
            ("Message pair", "cap_alpha", "steer", "peer"),
            ("Contrat relais", "hop_1", "handoff", "handoff"),
        ]
        for body, sender, kind, expected_origin in cases:
            wrapped = format_echo_message(body, sender=sender, kind=kind)
            self.assertTrue(wrapped.startswith("<ECHO_MESSAGE "))
            self.assertTrue(wrapped.endswith("</ECHO_MESSAGE>"))
            # Idempotence stricte
            self.assertEqual(format_echo_message(wrapped, sender="other", kind="steer"), wrapped)
            origin, extracted = extract_turn_origin(wrapped)
            self.assertEqual(origin, expected_origin)
            self.assertEqual(extracted, body)

        # Rétrocompatibilité stricte sur les 4 anciennes balises
        legacy_cases = [
            ("<REQUETE_ORCHESTRATEUR>\nOrdre 1\n</REQUETE_ORCHESTRATEUR>", "orchestrator", "Ordre 1"),
            ("<REQUETE_UTILISATEUR>\nOrdre 2\n</REQUETE_UTILISATEUR>", "human", "Ordre 2"),
            ('<MESSAGE_PAIR de="cap_2">\nPing pair\n</MESSAGE_PAIR>', "peer", "Ping pair"),
            ('<CONTRAT_RELAIS de="hop_1" vers="hop_2">\nRelais legacy\n</CONTRAT_RELAIS>', "handoff", "Relais legacy"),
        ]
        for raw_xml, expected_origin, expected_body in legacy_cases:
            origin, extracted = extract_turn_origin(raw_xml)
            self.assertEqual(origin, expected_origin)
            self.assertEqual(extracted, expected_body)

    # ------------------------------------------------------------------
    # Bloc 3 : Séparation SQLite subagent_meta vs subagent_sys & purge atomique
    # ------------------------------------------------------------------
    def test_03_sqlite_meta_sys_separation_and_hard_delete(self) -> None:
        with _IsolatedEchoEnv():
            chat_id = "c_meta_sep"
            sid = "thread_council_c1_expert_a"
            state = EchoStateManager(user_id=self.user_id, chat_id=chat_id)

            save_subagent_meta(
                state,
                sid,
                {
                    "sub_sid": sid,
                    "chat_id": chat_id,
                    "engine": "council",
                    "group_id": "c1",
                    "system_prompt": "PROMPT_BASE_A",
                    "final_system": "FINAL_PROMPT_B",
                },
            )
            raw_meta = state.get_session_setting(f"subagent_meta:{sid}")
            self.assertIsNotNone(raw_meta)
            parsed_meta = json.loads(raw_meta)
            self.assertNotIn("system_prompt", parsed_meta)
            self.assertNotIn("final_system", parsed_meta)

            self.assertEqual(get_subagent_system_prompt(state, sid), "FINAL_PROMPT_B")
            self.assertEqual(state.get_session_setting(f"subagent_sys:{sid}"), "FINAL_PROMPT_B")

            # Vérification save_subagent_system_prompt direct
            save_subagent_system_prompt(state, sid, "FINAL_PROMPT_C")
            self.assertEqual(get_subagent_system_prompt(state, sid), "FINAL_PROMPT_C")

            # Ajout d'étapes et de council_state:c1 puis suppression atomique
            state.save_thread_step(sid, chat_id, "council", 0, "user", [{"text": "Q1"}])
            state.save_session_setting("subagent_queue:" + sid, json.dumps([{"id": "1"}]))
            state.save_session_setting("council_state:c1", json.dumps({"session_num": 1}))

            deleted_cnt = _hard_delete_subagents_sync(state, [sid])
            self.assertEqual(deleted_cnt, 1)
            self.assertIsNone(state.get_session_setting(f"subagent_meta:{sid}"))
            self.assertIsNone(state.get_session_setting(f"subagent_sys:{sid}"))
            self.assertIsNone(state.get_session_setting(f"subagent_queue:{sid}"))
            self.assertIsNone(state.get_session_setting("council_state:c1"))
            self.assertEqual(state.get_thread_history(sid), [])

    # ------------------------------------------------------------------
    # Bloc 4 : allowed_tools dynamique, refresh_allowed_tools & garde Hoplite
    # ------------------------------------------------------------------
    async def test_04_allowed_tools_resolution_hot_refresh_and_hoplite_guard(self) -> None:
        # 4a. resolve_updated_allowed_tools
        self.assertEqual(resolve_updated_allowed_tools(["a", "b"], None), ["a", "b"])
        self.assertIsNone(resolve_updated_allowed_tools(None, None))
        self.assertIsNone(resolve_updated_allowed_tools(["a"], ["*"]))
        self.assertEqual(resolve_updated_allowed_tools(["a"], []), [])
        self.assertEqual(resolve_updated_allowed_tools(["a"], ["none"]), [])
        self.assertEqual(
            resolve_updated_allowed_tools(["search_web", "read_codex"], ["+code_executor", "-search_web"]),
            ["read_codex", "code_executor"],
        )
        self.assertEqual(
            resolve_updated_allowed_tools(["search_web"], ["edit_codex", "list_codex"]),
            ["edit_codex", "list_codex"],
        )

        # 4b. Rafraîchissement à chaud dans StandardToolDispatcher via run_slice + garde Hoplite
        with _IsolatedEchoEnv():
            chat_id = "c_allowed_hot"
            sid = "dlg_hot_1"
            state = EchoStateManager(user_id=self.user_id, chat_id=chat_id)
            captured_tool_names: list[list[str]] = []

            async def _hoplite_mutate_tool(**kwargs):
                # Simule un steer de l'Orchestrateur modifiant allowed_tools en vol
                # ET vérifie le refus si l'Hoplite tente lui-même une mutation
                hoplite_res = await EchoSubagentController.execute_control_action(
                    action="steer",
                    user_id=self.user_id,
                    chat_id=chat_id,
                    target_ids=[sid],
                    allowed_tools=["*"],
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )
                self.assertEqual((hoplite_res.get("status") or {}).get("status"), "error")

                # Mutation autorisée côté Orchestrateur (hors ECHO_SUBAGENT_CONTEXT)
                tok = echo_constants.ECHO_SUBAGENT_CONTEXT.set({})
                try:
                    orch_res = await EchoSubagentController.execute_control_action(
                        action="steer",
                        user_id=self.user_id,
                        chat_id=chat_id,
                        target_ids=[sid],
                        directive="Utiliser code_executor désormais.",
                        allowed_tools=["+code_executor", "-search_web"],
                        __user__=self.user_dict,
                        __metadata__={"chat_id": chat_id},
                    )
                    self.assertEqual((orch_res.get("status") or {}).get("status"), "success")
                finally:
                    echo_constants.ECHO_SUBAGENT_CONTEXT.reset(tok)
                return {"status": "success"}

            tools_pool = {
                "search_web": _make_tool_entry("search_web", _hoplite_mutate_tool),
                "code_executor": _make_tool_entry("code_executor"),
                "tool_advisor": _make_tool_entry("tool_advisor"),
            }
            register_chat_tools(chat_id, tools_pool)

            call_idx = 0

            async def _fake_cascade(*, payload, **kwargs):
                nonlocal call_idx
                call_idx += 1
                decls = []
                for t in payload.get("tools") or []:
                    for fd in t.get("functionDeclarations") or []:
                        decls.append(fd["name"])
                captured_tool_names.append(sorted(decls))
                if call_idx == 1:
                    return (
                        {
                            "candidates": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [
                                            {"functionCall": {"id": "fc1", "name": "search_web", "args": {}}}
                                        ],
                                    }
                                }
                            ]
                        },
                        "MODEL_APOLLO",
                        None,
                    )
                return (
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": "Terminé."}]}}]},
                    "MODEL_APOLLO",
                    None,
                )

            with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_fake_cascade):
                res = await EchoSubagentController.run_or_resume(
                    user_id=self.user_id,
                    chat_id=chat_id,
                    task="Tester refresh_allowed_tools",
                    sub_sid=sid,
                    allowed_tools=["search_web"],
                    can_delegate=False,
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )
            self.assertEqual((res.get("status") or {}).get("status"), "success")
            self.assertEqual(len(captured_tool_names), 2)
            # Tour 1 : search_web + tool_advisor (pas code_executor)
            self.assertIn("search_web", captured_tool_names[0])
            self.assertIn("tool_advisor", captured_tool_names[0])
            self.assertNotIn("code_executor", captured_tool_names[0])
            # Tour 2 (après refresh_allowed_tools en vol) : code_executor + tool_advisor (plus search_web)
            self.assertIn("code_executor", captured_tool_names[1])
            self.assertIn("tool_advisor", captured_tool_names[1])
            self.assertNotIn("search_web", captured_tool_names[1])
            meta_final = get_subagent_meta(state, sid) or {}
            self.assertEqual(meta_final.get("allowed_tools"), ["code_executor"])

    # ------------------------------------------------------------------
    # Bloc 5 : Cohorte légère, visibilité latérale, réveil wake_box sans deadlock, handoff Capitaine
    # ------------------------------------------------------------------
    async def test_05_cohort_lateral_visibility_wake_box_and_handoff_grade(self) -> None:
        with _IsolatedEchoEnv():
            chat_id = "c_cohort_phalanx"
            state = EchoStateManager(user_id=self.user_id, chat_id=chat_id)
            engine_tools = agent_engine_mod.Tools()

            async def _fake_simple_cascade(*, payload, **kwargs):
                return (
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": "OK."}]}}]},
                    "MODEL_APOLLO",
                    None,
                )

            # 5a. Cohorte légère via delegate_to_agent(..., cohort_id="audit")
            with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_fake_simple_cascade):
                res_cohort = await engine_tools.delegate_to_agent(
                    task="Audit initial",
                    sub_sid="cap_alpha",
                    can_delegate=True,
                    cohort_id="audit",
                    __user__=self.user_dict,
                    __chat_id__=chat_id,
                    __metadata__={"chat_id": chat_id},
                )
            self.assertEqual((res_cohort.get("status") or {}).get("status"), "success")
            meta_alpha = get_subagent_meta(state, "cap_alpha") or {}
            self.assertEqual(meta_alpha.get("group_id"), "phx_audit")
            self.assertEqual(meta_alpha.get("engine"), "phalanx")
            self.assertTrue(meta_alpha.get("can_delegate"))

            # Création d'un second Capitaine cap_beta dans la même cohorte
            save_subagent_meta(
                state,
                "cap_beta",
                {
                    "sub_sid": "cap_beta",
                    "chat_id": chat_id,
                    "engine": "phalanx",
                    "group_id": "phx_audit",
                    "depth": 2,
                    "can_delegate": True,
                    "status": "idle",
                },
            )
            state.save_thread_step("cap_beta", chat_id, "delegate", 0, "user", [{"text": "Init beta"}])
            state.save_thread_step("cap_beta", chat_id, "delegate", 1, "model", [{"text": "En veille"}])

            # 5b. Visibilité latérale Capitaine <-> Capitaine vs blocage kill hors descendants
            tok_cap = echo_constants.ECHO_SUBAGENT_CONTEXT.set({
                "is_subagent": True,
                "sub_sid": "cap_alpha",
                "group_id": "phx_audit",
                "depth": 2,
                "can_delegate": True,
            })
            try:
                lat_visible = resolve_target_sub_sids(
                    state, chat_id, target_ids=["cap_beta"], lateral_ok=True
                )
                self.assertEqual(lat_visible, ["cap_beta"])
                non_lat_blocked = resolve_target_sub_sids(
                    state, chat_id, target_ids=["cap_beta"], lateral_ok=False
                )
                self.assertEqual(non_lat_blocked, [])
            finally:
                echo_constants.ECHO_SUBAGENT_CONTEXT.reset(tok_cap)

            # 5c. Réveil post-tranche sans deadlock via wake_box sous sémaphore saturé (4/4 slots)
            woken_peers: list[str] = []

            async def _peer_steer_tool(**kwargs):
                res_s = await EchoSubagentController.execute_control_action(
                    action="steer",
                    user_id=self.user_id,
                    chat_id=chat_id,
                    target_ids=["cap_beta"],
                    directive="Réveil pair sans deadlock",
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )
                self.assertEqual((res_s.get("status") or {}).get("wake_scheduled_sids"), ["cap_beta"])
                return res_s

            register_chat_tools(
                chat_id,
                {"control_subagents": _make_tool_entry("control_subagents", _peer_steer_tool)},
            )

            # On sature 3 slots du sémaphore depth=2 (le 4ème sera pris par cap_alpha) -> 4/4 occupés !
            sem_d2 = get_chat_subagent_semaphore(chat_id, 2)
            for _ in range(3):
                await sem_d2.acquire()

            wake_call_idx = 0

            async def _wake_cascade(*, payload, **kwargs):
                nonlocal wake_call_idx
                wake_call_idx += 1
                if wake_call_idx == 1:
                    return (
                        {
                            "candidates": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [
                                            {
                                                "functionCall": {
                                                    "id": "fc_steer",
                                                    "name": "control_subagents",
                                                    "args": {},
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
                if wake_call_idx == 2:
                    return (
                        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Alpha fini."}]}}]},
                        "MODEL_APOLLO",
                        None,
                    )
                # 3ème appel LLM = cap_beta réveillé après libération du slot de cap_alpha !
                woken_peers.append("cap_beta_woken")
                return (
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": "Beta réveillé."}]}}]},
                    "MODEL_APOLLO",
                    None,
                )

            try:
                with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_wake_cascade):
                    res_wake = await asyncio.wait_for(
                        EchoSubagentController.run_or_resume(
                            user_id=self.user_id,
                            chat_id=chat_id,
                            task="Envoyer steer à cap_beta",
                            sub_sid="cap_alpha",
                            engine="phalanx",
                            group_id="phx_audit",
                            depth=2,
                            can_delegate=True,
                            __user__=self.user_dict,
                            __metadata__={"chat_id": chat_id},
                        ),
                        timeout=5.0,
                    )
                self.assertEqual((res_wake.get("status") or {}).get("status"), "success")
                self.assertEqual(woken_peers, ["cap_beta_woken"])
            finally:
                for _ in range(3):
                    sem_d2.release()

            # 5d. Préservation de can_delegate=True sur _tail_handoff vers cap_beta
            async def _peer_handoff_tool(**kwargs):
                return await EchoSubagentController.execute_control_action(
                    action="handoff",
                    user_id=self.user_id,
                    chat_id=chat_id,
                    target_ids=["cap_beta"],
                    handoff={"acquis": "Phase 1 OK", "ressources": ["r1"], "mission": "Suite Capitaine"},
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )

            register_chat_tools(
                chat_id,
                {"control_subagents": _make_tool_entry("control_subagents", _peer_handoff_tool)},
            )
            hf_idx = 0

            async def _handoff_cascade(*, payload, **kwargs):
                nonlocal hf_idx
                hf_idx += 1
                if hf_idx == 1:
                    return (
                        {
                            "candidates": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [
                                            {"functionCall": {"id": "fc_hf", "name": "control_subagents", "args": {}}}
                                        ],
                                    }
                                }
                            ]
                        },
                        "MODEL_APOLLO",
                        None,
                    )
                return (
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": "Relais exécuté."}]}}]},
                    "MODEL_APOLLO",
                    None,
                )

            with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_handoff_cascade):
                res_hf = await EchoSubagentController.run_or_resume(
                    user_id=self.user_id,
                    chat_id=chat_id,
                    task="Passer le relais à cap_beta",
                    sub_sid="cap_alpha",
                    engine="phalanx",
                    group_id="phx_audit",
                    depth=2,
                    can_delegate=True,
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )
            self.assertEqual((res_hf.get("status") or {}).get("sub_sid"), "cap_beta")
            meta_beta_after = get_subagent_meta(state, "cap_beta") or {}
            self.assertTrue(meta_beta_after.get("can_delegate"))
            self.assertEqual(meta_beta_after.get("member_role"), "captain")

    # ------------------------------------------------------------------
    # Bloc 6 : Déblocage spécialistes hiérarchiques, propagation & tool_advisor
    # ------------------------------------------------------------------
    async def test_06_hierarchical_specialists_propagation_and_tool_advisor(self) -> None:
        with _IsolatedEchoEnv():
            chat_id = "c_hier_spec"
            pool = {
                name: _make_tool_entry(name)
                for name in (
                    list(echo_constants.DELEGATE_HIERARCHICAL_SPECIALIST_TOOLS)
                    + ["read_codex", "ask_user_input", "tool_advisor"]
                )
            }
            register_chat_tools(chat_id, pool)

            # 6a. resolve_delegate_tools avec can_delegate=False vs True
            tools_hoplite, _ = resolve_delegate_tools(
                chat_id=chat_id, can_delegate=False, __metadata__={"chat_id": chat_id}
            )
            blacklisted_hierarchical = (
                set(echo_constants.DELEGATE_HIERARCHICAL_SPECIALIST_TOOLS)
                & set(echo_constants.DELEGATE_AGENT_BLACKLIST)
            )
            for spec_tool in blacklisted_hierarchical:
                self.assertNotIn(spec_tool, tools_hoplite)
            self.assertIn("read_codex", tools_hoplite)
            self.assertNotIn("ask_user_input", tools_hoplite)

            tools_captain, _ = resolve_delegate_tools(
                chat_id=chat_id, can_delegate=True, __metadata__={"chat_id": chat_id}
            )
            for spec_tool in echo_constants.DELEGATE_HIERARCHICAL_SPECIALIST_TOOLS:
                self.assertIn(spec_tool, tools_captain)
            self.assertNotIn("ask_user_input", tools_captain)

            # 6b. Propagation (parent_sid, group_id, depth+1) sur ask_data_broker / delegate_to_n8n_grapher / delegate_deep_research
            captured_runs: list[dict] = []

            async def _fake_run_or_resume(**kwargs):
                captured_runs.append({
                    "engine": kwargs.get("engine"),
                    "parent_sid": kwargs.get("parent_sid"),
                    "group_id": kwargs.get("group_id"),
                    "depth": kwargs.get("depth"),
                })
                return {"text": "OK", "status": {"status": "success"}}

            db_tools = data_broker_mod.Tools()
            n8n_tools = n8n_orch_mod.Tools()
            search_tools = web_search_mod.Tools()

            tok = echo_constants.ECHO_SUBAGENT_CONTEXT.set({
                "is_subagent": True,
                "sub_sid": "cap_parent",
                "group_id": "phx_team",
                "depth": 2,
                "can_delegate": True,
                "sub_tools_names": list(tools_captain.keys()),
            })
            try:
                with mock.patch.object(EchoSubagentController, "run_or_resume", side_effect=_fake_run_or_resume):
                    await db_tools.ask_data_broker(
                        query="Collecter métriques",
                        __user__=self.user_dict,
                        __chat_id__=chat_id,
                        __metadata__={"chat_id": chat_id},
                    )
                    await n8n_tools.delegate_to_n8n_grapher(
                        instructions="Construire graphe",
                        __user__=self.user_dict,
                        __metadata__={"chat_id": chat_id},
                    )
                    with mock.patch.object(echo_browser_lib.EchoBrowserLib, "reset_session", return_value=None):
                        await search_tools.delegate_deep_research(
                            query="Recherche approfondie",
                            __user__=self.user_dict,
                            __chat_id__=chat_id,
                            __metadata__={"chat_id": chat_id},
                        )

                self.assertEqual(len(captured_runs), 3)
                for call_info, expected_eng in zip(
                    captured_runs, ("data_broker", "n8n_grapher", "deep_research")
                ):
                    self.assertEqual(call_info["engine"], expected_eng)
                    self.assertEqual(call_info["parent_sid"], "cap_parent")
                    self.assertEqual(call_info["group_id"], "phx_team")
                    self.assertEqual(call_info["depth"], 3)

                # 6c. tool_advisor ne bloque pas les outils ouverts au sous-agent
                adv_payloads: list[dict] = []

                async def _adv_cascade(*, payload, **kwargs):
                    adv_payloads.append(copy.deepcopy(payload))
                    return (
                        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Conseil OK"}]}}]},
                        "MODEL_APOLLO",
                        None,
                    )

                gen_tools = generalist_mod.Tools()
                with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_adv_cascade):
                    await gen_tools.tool_advisor(
                        objective="Utiliser ask_data_broker et delegate_to_n8n_grapher",
                        target_tools="ask_data_broker,delegate_to_n8n_grapher",
                        __user__=self.user_dict,
                        __chat_id__=chat_id,
                        __metadata__={"chat_id": chat_id},
                    )
                self.assertEqual(len(adv_payloads), 1)
                sys_txt = adv_payloads[0]["systemInstruction"]["parts"][0]["text"]
                # Extrait la ligne "Outils STRICTEMENT INTERDITS à cet appelant : ..."
                forbidden_line = next(
                    (ln for ln in sys_txt.splitlines() if "Outils STRICTEMENT INTERDITS" in ln),
                    "",
                )
                self.assertTrue(forbidden_line)
                self.assertNotIn("ask_data_broker", forbidden_line)
                self.assertNotIn("delegate_to_n8n_grapher", forbidden_line)
                self.assertIn("ask_user_input", forbidden_line)
                self.assertIn("`ask_data_broker`", sys_txt)
                self.assertIn("`delegate_to_n8n_grapher`", sys_txt)
            finally:
                echo_constants.ECHO_SUBAGENT_CONTEXT.reset(tok)

    # ------------------------------------------------------------------
    # Bloc 7 : Concile de Delphes V2 (cache KV, historique 100%, unanimité, continuité)
    # ------------------------------------------------------------------
    async def test_07a_delphi_council_v2_kv_cache_history_partial_stab_and_session2(self) -> None:
        with _IsolatedEchoEnv():
            chat_id = "c_council_v2"
            cid = "arch-v2"
            # Crée deux skills réels sur disque
            echo_skills.save_skill(self.user_id, "expert_sec", "Expert Sécurité", "Audit sécurité", "Analyser les risques.")
            echo_skills.save_skill(self.user_id, "expert_perf", "Expert Performance", "Audit perf", "Analyser la latence.")

            orch_tools = agent_orch_mod.Tools()
            captured_calls: list[dict] = []

            async def _council_cascade(*, payload, target_model_key, **kwargs):
                sys_txt = (payload.get("systemInstruction") or {}).get("parts", [{}])[0].get("text", "")
                contents = copy.deepcopy(payload.get("contents") or [])
                captured_calls.append({
                    "sys": sys_txt,
                    "contents": contents,
                    "model": target_model_key,
                })
                last_txt = ""
                if contents:
                    last_txt = "".join(p.get("text", "") for p in (contents[-1].get("parts") or []))

                # Synthétiseur
                if "rapporteur officiel du conseil" in sys_txt:
                    ses_tag = "SÉANCE #2" if "SÉANCE #2" in last_txt else "SÉANCE #1"
                    resp_txt = f"Rapport {ses_tag}\n### État du Consensus & Suites Éventuelles\nDécision à l'Orchestrateur."
                else:
                    is_sec = "agit en tant que Participant 1 (Expert Sécurité)" in sys_txt
                    if "SÉANCE #1 — TOUR 1/" in last_txt:
                        resp_txt = "S1T1 Sec: Position initiale sécurité." if is_sec else "S1T1 Perf: Position initiale perf."
                    elif "SÉANCE #1 — TOUR 2/" in last_txt:
                        # Un seul expert sur 2 émet [POSITION_STABILISEE] -> pas d'arrêt anticipé !
                        if is_sec:
                            resp_txt = f"{echo_constants.ECHO_COUNCIL_STABILIZED_MARKER} Sec maintient."
                        else:
                            resp_txt = "S1T2 Perf: Désaccord argumenté sur latence."
                    elif "SÉANCE #1 — DERNIER TOUR DE TABLE" in last_txt:
                        resp_txt = (
                            "### Analyse\nS1T3 Sec\n### Dialectique\nOk\n### Réponse\nConclusion finale Sec S1"
                            if is_sec
                            else "### Analyse\nS1T3 Perf\n### Dialectique\nOk\n### Réponse\nConclusion finale Perf S1"
                        )
                    elif "SÉANCE #2 — TOUR 1/" in last_txt:
                        resp_txt = "S2T1 Sec: Reprise séance 2." if is_sec else "S2T1 Perf: Reprise séance 2."
                    elif "SÉANCE #2 — TOUR 2/" in last_txt:
                        resp_txt = "S2T2 Sec: Débat S2." if is_sec else "S2T2 Perf: Débat S2."
                    else:
                        resp_txt = "S2T3 Conclusion finale."

                return (
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": resp_txt}]}}]},
                    target_model_key,
                    None,
                )

            with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_council_cascade):
                out_s1 = await orch_tools.consult_council(
                    question="Architecture Zero-Trust ?",
                    participants=["expert_sec", "expert_perf"],
                    council_id=cid,
                    rounds=3,
                    close_on_finish=False,
                    __user__=self.user_dict,
                    __chat_id__=chat_id,
                    __metadata__={"chat_id": chat_id},
                )
                out_s2 = await orch_tools.consult_council(
                    question="Impact sur le P99 ?",
                    participants=["expert_sec", "expert_perf"],
                    council_id=cid,
                    rounds=3,
                    close_on_finish=False,
                    __user__=self.user_dict,
                    __chat_id__=chat_id,
                    __metadata__={"chat_id": chat_id},
                )

            out_s1_txt = out_s1["text"] if isinstance(out_s1, dict) else str(out_s1)
            out_s2_txt = out_s2["text"] if isinstance(out_s2, dict) else str(out_s2)
            self.assertIn("Séance #1 (2 experts, 3 tours)", out_s1_txt)
            self.assertIn("Séance #2 (2 experts, 3 tours)", out_s2_txt)

            # Séparer les appels par rôle (expert_sec, expert_perf, synthétiseur)
            sec_calls = [c for c in captured_calls if "agit en tant que Participant 1 (Expert Sécurité)" in c["sys"]]
            perf_calls = [c for c in captured_calls if "agit en tant que Participant 2 (Expert Performance)" in c["sys"]]
            self.assertEqual(len(sec_calls), 6)   # 3 tours S1 + 3 tours S2
            self.assertEqual(len(perf_calls), 6)  # 3 tours S1 + 3 tours S2

            # 1. systemInstruction identique octet par octet entre T1, T2, T3 et Séance #2
            self.assertEqual(len({c["sys"] for c in sec_calls}), 1)
            self.assertEqual(len({c["sys"] for c in perf_calls}), 1)

            # 2. Historique 100 % complet conservé (croissance stricte 1 -> 3 -> 5 -> 7 -> 9 -> 11 messages)
            self.assertEqual([len(c["contents"]) for c in sec_calls], [1, 3, 5, 7, 9, 11])
            self.assertEqual([len(c["contents"]) for c in perf_calls], [1, 3, 5, 7, 9, 11])

            # 3. Directive CONCLUSION DÉFINITIVE et interdiction [POSITION_STABILISEE] sur le dernier tour nominal
            s1_t3_prompt = "".join(p.get("text", "") for p in sec_calls[2]["contents"][-1]["parts"])
            self.assertIn("CONCLUSION DÉFINITIVE", s1_t3_prompt)
            self.assertIn(f"`{echo_constants.ECHO_COUNCIL_STABILIZED_MARKER}` ABSOLUMENT INTERDIT sur ce dernier tour", s1_t3_prompt)

            # 4. Séance #2 Tour 1 injecte la conclusion finale manquante de l'autre expert au Tour 3 de Séance #1
            s2_t1_sec_prompt = "".join(p.get("text", "") for p in sec_calls[3]["contents"][-1]["parts"])
            self.assertIn("CONCLUSIONS FINALES DE LA SÉANCE #1", s2_t1_sec_prompt)
            self.assertIn("Conclusion finale Perf S1", s2_t1_sec_prompt)

            # 5. Conservation des deux synthèses (step 0..1 et step 2..3) dans thread_council_<cid>_synthesis
            state = EchoStateManager(user_id=self.user_id, chat_id=chat_id)
            synth_hist = state.get_thread_history(f"thread_council_{cid}_synthesis")
            self.assertEqual(len(synth_hist), 4)
            self.assertIn("Rapport SÉANCE #1", synth_hist[1]["parts"][0]["text"])
            self.assertIn("Rapport SÉANCE #2", synth_hist[3]["parts"][0]["text"])

    async def test_07b_delphi_council_v2_unanimous_early_stop(self) -> None:
        with _IsolatedEchoEnv():
            chat_id = "c_council_unanimous"
            cid = "unanimous-ccl"
            echo_skills.save_skill(self.user_id, "exp_a", "Expert A", "Domaine A", "Instr A.")
            echo_skills.save_skill(self.user_id, "exp_b", "Expert B", "Domaine B", "Instr B.")

            orch_tools = agent_orch_mod.Tools()
            expert_rounds_seen = 0

            async def _unanimous_cascade(*, payload, target_model_key, **kwargs):
                nonlocal expert_rounds_seen
                sys_txt = (payload.get("systemInstruction") or {}).get("parts", [{}])[0].get("text", "")
                last_txt = "".join(
                    p.get("text", "") for p in ((payload.get("contents") or [])[-1].get("parts") or [])
                )
                if "rapporteur officiel du conseil" in sys_txt:
                    return (
                        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Synthèse unanime."}]}}]},
                        target_model_key,
                        None,
                    )
                expert_rounds_seen += 1
                if "TOUR 2/" in last_txt:
                    txt = f"{echo_constants.ECHO_COUNCIL_STABILIZED_MARKER} Accord total."
                else:
                    txt = "Analyse tour 1."
                return (
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": txt}]}}]},
                    target_model_key,
                    None,
                )

            with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_unanimous_cascade):
                out = await orch_tools.consult_council(
                    question="Choix consensus ?",
                    participants=["exp_a", "exp_b"],
                    council_id=cid,
                    rounds=3,
                    close_on_finish=False,
                    __user__=self.user_dict,
                    __chat_id__=chat_id,
                    __metadata__={"chat_id": chat_id},
                )

            # 2 experts × 2 tours = 4 appels experts (Tour 3 non exécuté)
            out_txt = out["text"] if isinstance(out, dict) else str(out)
            self.assertEqual(expert_rounds_seen, 4)
            self.assertIn("Séance #1 (2 experts, 2 tours)", out_txt)

    async def test_08_long_sub_sid_and_council_multiplier_no_collision(self) -> None:
        long_sid = "thread_phalanx_test-c-depth3-hierarchy_strategist"
        self.assertEqual(len(long_sid), 49)
        self.assertLessEqual(len(long_sid), echo_constants.ECHO_MAX_SUBAGENT_SID_LEN)
        self.assertEqual(validate_or_create_sub_sid(long_sid), long_sid)

        async def _direct_cascade(*, payload, target_model_key, **kwargs):
            return (
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "Réponse finale directe."}]}}]},
                target_model_key,
                None,
            )

        with _IsolatedEchoEnv():
            chat_id = "c_long_phx_sid"
            with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_direct_cascade):
                await agent_orch_mod.Tools().consult_phalanx(
                    objective="Mission",
                    phalanx_id="test-c-depth3-hierarchy",
                    __user__=self.user_dict,
                    __chat_id__=chat_id,
                    __metadata__={"chat_id": chat_id},
                )
            state = EchoStateManager(user_id=self.user_id, chat_id=chat_id)
            self.assertEqual(
                resolve_target_sub_sids(state, chat_id, target_ids=[long_sid]),
                [long_sid],
            )

        with _IsolatedEchoEnv():
            chat_id = "c_council_mult3"
            echo_skills.save_skill(
                self.user_id,
                "expert_gouvernance_risques",
                "Expert Gouvernance & Risques",
                "Analyse des risques et gouvernance.",
                "Analyser les risques.",
            )
            with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_direct_cascade):
                await agent_orch_mod.Tools().consult_council(
                    question="Test multi",
                    participants=["expert_gouvernance_risques"],
                    council_id="ccl-test-proxmox-mig",
                    multiplier=3,
                    rounds=3,
                    __user__=self.user_dict,
                    __chat_id__=chat_id,
                    __metadata__={"chat_id": chat_id},
                )
            state = EchoStateManager(user_id=self.user_id, chat_id=chat_id)
            raw_threads = state.list_threads(chat_id)
            thread_ids = [
                (t.get("thread_id") or t.get("sub_sid")) if isinstance(t, dict) else str(t)
                for t in raw_threads
            ]
            expert_sids = [
                tid for tid in thread_ids if tid.startswith("thread_council_ccl-test-proxmox-mig_m")
            ]
            self.assertEqual(len(expert_sids), 3)
            self.assertEqual(len(set(expert_sids)), 3)
            self.assertTrue(any(tid.startswith("thread_council_ccl-test-proxmox-mig_m1_") for tid in expert_sids))
            self.assertTrue(any(tid.startswith("thread_council_ccl-test-proxmox-mig_m2_") for tid in expert_sids))
            self.assertTrue(any(tid.startswith("thread_council_ccl-test-proxmox-mig_m3_") for tid in expert_sids))
            self.assertTrue(all(len(tid) <= 96 for tid in expert_sids))
            self.assertIn("thread_council_ccl-test-proxmox-mig_synthesis", thread_ids)

    async def test_09_resume_and_captain_handoff_preserve_slice_limits_and_pas(self) -> None:
        with _IsolatedEchoEnv():
            chat_id = "c_resume_handoff"
            register_chat_tools(
                chat_id,
                {
                    "delegate_to_agent": _make_tool_entry("delegate_to_agent"),
                    "control_subagents": _make_tool_entry("control_subagents"),
                },
            )

            async def _ok_cascade(*, payload, target_model_key, **kwargs):
                return (
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": "Étape exécutée."}]}}]},
                    target_model_key,
                    None,
                )

            with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_ok_cascade):
                await EchoSubagentController.run_or_resume(
                    user_id=self.user_id,
                    chat_id=chat_id,
                    task="Piloter front A",
                    sub_sid="cap_a",
                    engine="phalanx",
                    group_id="phx_crise",
                    can_delegate=True,
                    depth=2,
                    slice_max_turns=15,
                    slice_timeout_sec=300,
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )
                await EchoSubagentController.run_or_resume(
                    user_id=self.user_id,
                    chat_id=chat_id,
                    task="Exécuter tâche 1",
                    sub_sid="hop_1",
                    engine="phalanx",
                    group_id="phx_crise",
                    can_delegate=False,
                    depth=2,
                    slice_max_turns=8,
                    slice_timeout_sec=120,
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )
                await EchoSubagentController.run_or_resume(
                    user_id=self.user_id,
                    chat_id=chat_id,
                    task="Piloter front B",
                    sub_sid="cap_b",
                    engine="phalanx",
                    group_id="phx_crise",
                    can_delegate=True,
                    depth=2,
                    slice_max_turns=15,
                    slice_timeout_sec=300,
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )

                await EchoSubagentController.execute_control_action(
                    action="resume",
                    user_id=self.user_id,
                    chat_id=chat_id,
                    target_ids=["cap_a"],
                    directive="Suite",
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )
                state = EchoStateManager(user_id=self.user_id, chat_id=chat_id)
                cap_a_meta = get_subagent_meta(state, "cap_a") or {}
                self.assertEqual(cap_a_meta.get("slice_turns_limit"), 15)
                self.assertEqual(cap_a_meta.get("slice_timeout_sec"), 300)

                await EchoSubagentController.execute_control_action(
                    action="resume",
                    user_id=self.user_id,
                    chat_id=chat_id,
                    target_ids=["hop_1"],
                    directive="Suite",
                    __user__=self.user_dict,
                    __metadata__={"chat_id": chat_id},
                )
                hop_1_meta = get_subagent_meta(state, "hop_1") or {}
                self.assertEqual(hop_1_meta.get("slice_turns_limit"), 8)
                self.assertEqual(hop_1_meta.get("slice_timeout_sec"), 120)

                token = echo_constants.ECHO_SUBAGENT_CONTEXT.set({
                    "is_subagent": True,
                    "sub_sid": "cap_a",
                    "group_id": "phx_crise",
                    "depth": 2,
                    "can_delegate": True,
                })
                try:
                    await EchoSubagentController.execute_control_action(
                        action="handoff",
                        user_id=self.user_id,
                        chat_id=chat_id,
                        target_ids=["cap_b"],
                        handoff={"acquis": "A", "ressources": ["r1"], "mission": "M"},
                        __user__=self.user_dict,
                        __metadata__={"chat_id": chat_id},
                    )
                finally:
                    echo_constants.ECHO_SUBAGENT_CONTEXT.reset(token)

                cap_b_meta = get_subagent_meta(state, "cap_b") or {}
                self.assertIs(cap_b_meta.get("can_delegate"), True)
                self.assertEqual(cap_b_meta.get("slice_turns_limit"), 15)

        self.assertIn("Le Modèle DOIT poursuivre", echo_prompts.USR_ORCHESTRATOR_SLICE_CONTINUE)
        self.assertNotIn("poursuis", echo_prompts.USR_ORCHESTRATOR_SLICE_CONTINUE.lower())
        self.assertNotIn("ISOLATION SANDBOX", echo_prompts.SYS_ORCHESTRATOR_PHALANX)
        self.assertNotIn("SOBRIÉTÉ TOKENS", echo_prompts.SYS_ORCHESTRATOR_PHALANX)

    async def test_10_tool_advisor_subagent_allowed_tools_and_phalanx_context(self) -> None:
        excluded = compute_subagent_excluded_tools(
            {"sub_tools_names": ["read_codex", "tool_advisor"]},
            ["read_codex", "create_codex", "code_executor", "tool_advisor"],
        )
        self.assertIn("create_codex", excluded)
        self.assertIn("code_executor", excluded)
        self.assertIn("tool_advisor", excluded)
        self.assertNotIn("read_codex", excluded)

        with _IsolatedEchoEnv():
            chat_id = "c_tool_advisor_phx"
            register_chat_tools(
                chat_id,
                {
                    "read_codex": _make_tool_entry("read_codex"),
                    "create_codex": _make_tool_entry("create_codex"),
                    "code_executor": _make_tool_entry("code_executor"),
                    "tool_advisor": _make_tool_entry("tool_advisor"),
                },
            )
            captured_sys: list[str] = []

            async def _advisor_cascade(*, payload, target_model_key, **kwargs):
                sys_txt = (payload.get("systemInstruction") or {}).get("parts", [{}])[0].get("text", "")
                captured_sys.append(sys_txt)
                return (
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": "Conseil OK."}]}}]},
                    target_model_key,
                    None,
                )

            token = echo_constants.ECHO_SUBAGENT_CONTEXT.set({
                "is_subagent": True,
                "sub_sid": "hoplite_2_val",
                "group_id": "phx_demo",
                "depth": 2,
                "can_delegate": False,
                "sub_tools_names": ["read_codex", "tool_advisor"],
            })
            try:
                with mock.patch.object(EchoGeminiClient, "call_cascade", side_effect=_advisor_cascade):
                    await generalist_mod.Tools().tool_advisor(
                        objective="Lire un script dans codex sandbox",
                        target_tools="read_codex,code_executor",
                        __user__=self.user_dict,
                        __chat_id__=chat_id,
                        __metadata__={"chat_id": chat_id},
                    )
            finally:
                echo_constants.ECHO_SUBAGENT_CONTEXT.reset(token)

            self.assertEqual(len(captured_sys), 1)
            sys_txt = captured_sys[0]
            caller_block = sys_txt.split("<caller_context>", 1)[1].split("</caller_context>", 1)[0]
            hmap_block = sys_txt.split("<hierarchical_domains_map>", 1)[1].split("</hierarchical_domains_map>", 1)[0]

            self.assertIn("sandbox/hoplite_2_val/", caller_block)
            self.assertIn("can_delegate=False", caller_block)
            self.assertIn("Outils STRICTEMENT INTERDITS", caller_block)
            self.assertIn("code_executor", caller_block)
            self.assertIn("create_codex", caller_block)

            self.assertIn("Outils INTERDITS pour l'appelant courant", hmap_block)
            self.assertIn("code_executor", hmap_block)
            self.assertIn("read_codex", hmap_block)

    def test_11_describe_empty_llm_response_agnostic(self):
        d1 = describe_empty_llm_response(
            {"promptFeedback": {"blockReason": "OTHER", "blockReasonMessage": "Blocked"}}
        )
        self.assertTrue(d1["is_prompt_blocked"])
        self.assertEqual(d1["kind"], "prompt_blocked")
        self.assertEqual(d1["reason"], "OTHER")
        self.assertEqual(d1["raw_message"], "Blocked")
        self.assertNotIn("Google", d1["summary"])
        self.assertNotIn("Gemini", d1["summary"])

        d2 = describe_empty_llm_response(
            {"response": {"promptFeedback": {"blockReason": "SAFETY"}}}
        )
        self.assertTrue(d2["is_prompt_blocked"])
        self.assertEqual(d2["reason"], "SAFETY")

        d3 = describe_empty_llm_response(
            {"candidates": [{"finishReason": "MALFORMED_FUNCTION_CALL"}]}
        )
        self.assertFalse(d3["is_prompt_blocked"])
        self.assertEqual(d3["kind"], "generation_stopped")
        self.assertEqual(d3["reason"], "MALFORMED_FUNCTION_CALL")
        self.assertNotIn("Google", d3["summary"])
        self.assertNotIn("Gemini", d3["summary"])

        for empty_payload in ({}, None):
            d_empty = describe_empty_llm_response(empty_payload)
            self.assertFalse(d_empty["is_prompt_blocked"])
            self.assertEqual(d_empty["kind"], "empty")
            self.assertEqual(d_empty["reason"], "EMPTY_RESPONSE")

    async def test_12_call_cascade_falls_back_on_prompt_blocked_and_raises_if_all_blocked(self):
        with _IsolatedEchoEnv():
            err = PromptBlockedError(
                "OTHER",
                "Requête bloquée par les filtres de l'API d'inférence (Motif : OTHER).",
            )
            ok_data = {"candidates": [{"content": {"role": "model", "parts": [{"text": "OK_ZEPHYR_FALLBACK"}]}}]}

            # 1. Faux positif isolé sur MODEL_APOLLO -> repli automatique sur MODEL_ZEPHYR
            with mock.patch.object(
                EchoGeminiClient, "call", new=mock.AsyncMock(side_effect=[err, ok_data])
            ) as mock_call:
                data, model_used, reason = await EchoGeminiClient.call_cascade(
                    target_model_key="MODEL_APOLLO",
                    payload={"contents": [{"role": "user", "parts": [{"text": "test"}]}]},
                    user_id=self.user_id,
                    metadata={"_echo_cognitive_policy": "AUTO:MODEL_ATHENA"},
                )
                self.assertEqual(mock_call.call_count, 2)
                self.assertEqual(model_used, "MODEL_ZEPHYR")
                self.assertIn("blocked", reason or "")
                self.assertEqual(data["candidates"][0]["content"]["parts"][0]["text"], "OK_ZEPHYR_FALLBACK")

            # 2. Tous les paliers bloquent -> relève PromptBlockedError à la fin
            with mock.patch.object(
                EchoGeminiClient, "call", new=mock.AsyncMock(side_effect=err)
            ) as mock_call_all:
                with self.assertRaises(PromptBlockedError):
                    await EchoGeminiClient.call_cascade(
                        target_model_key="MODEL_APOLLO",
                        payload={"contents": [{"role": "user", "parts": [{"text": "test"}]}]},
                        user_id=self.user_id,
                        metadata={"_echo_cognitive_policy": "AUTO:MODEL_ATHENA"},
                    )
                self.assertEqual(mock_call_all.call_count, 2)

    async def test_13_subagent_prompt_blocked_rolls_back_orphan_user_step_before_resume(self):
        with _IsolatedEchoEnv():
            chat_id = "c_prompt_block_rollback"
            sid = "cap_rollback"
            sid_err = "cap_err_close"
            err = PromptBlockedError(
                "OTHER",
                "Requête bloquée par les filtres de l'API d'inférence (Motif : OTHER).",
            )
            with mock.patch.object(
                EchoGeminiClient, "call_cascade", new=mock.AsyncMock(side_effect=err)
            ):
                res1 = await EchoSubagentController.run_or_resume(
                    user_id=self.user_id,
                    chat_id=chat_id,
                    sub_sid=sid,
                    task="Consigne sensible",
                    target_model_key="MODEL_APOLLO",
                    system_prompt="Assistant test",
                    engine="specialist",
                )
                await EchoSubagentController.run_or_resume(
                    user_id=self.user_id,
                    chat_id=chat_id,
                    sub_sid=sid_err,
                    task="Consigne sensible à clore",
                    target_model_key="MODEL_APOLLO",
                    system_prompt="Assistant test",
                    engine="specialist",
                )

            self.assertEqual(res1["status"]["status"], "error")
            self.assertEqual(res1["status"]["block_reason"], "OTHER")
            state = EchoStateManager(user_id=self.user_id, chat_id=chat_id)
            self.assertEqual(state.get_thread_history(sid), [])
            self.assertEqual(state.get_thread_history(sid_err), [])
            visible = resolve_target_sub_sids(state, chat_id)
            self.assertIn(sid, visible)
            self.assertIn(sid_err, visible)

            # Clôture propre d'un sous-agent resté à 0 step après rejet initial
            close_res = await EchoSubagentController.execute_control_action(
                action="close",
                user_id=self.user_id,
                chat_id=chat_id,
                target_ids=[sid_err],
            )
            self.assertEqual(close_res["status"]["status"], "success")
            self.assertIn(sid_err, close_res["status"]["affected_sids"])
            self.assertNotIn(sid_err, resolve_target_sub_sids(state, chat_id))

            ok_resp = (
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "OK_REFORMULE"}]}}]},
                "MODEL_APOLLO",
                None,
            )
            with mock.patch.object(
                EchoGeminiClient, "call_cascade", new=mock.AsyncMock(return_value=ok_resp)
            ):
                await EchoSubagentController.run_or_resume(
                    user_id=self.user_id,
                    chat_id=chat_id,
                    sub_sid=sid,
                    task="Consigne reformulée",
                    target_model_key="MODEL_APOLLO",
                    system_prompt="Assistant test",
                    engine="specialist",
                )

            history = state.get_thread_history(sid)
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[1]["role"], "model")
            self.assertIn("OK_REFORMULE", history[1]["parts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
