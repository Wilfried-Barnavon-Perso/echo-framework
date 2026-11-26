# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Generalist Tools
author: Antigravity
version: 6.24.1.1
description: Composant système interne : ECHO Generalist Tools.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.24.1.1: Validation chronologique end >= start (_parse_calendar_dt_ns / _validate_calendar_bounds) dans manage_ui_calendar et statut explicite obligatoire dans manage_ui_tasks(action="update").
# 6.24.0.1: Consolidation polymorphe en manage_ui_calendar, manage_ui_automations et manage_ui_tasks (12 outils) et exclusion auto de echo_ui_guide dans tool_advisor.
# 6.23.11.1: Ajout des outils list_ui_chats et read_ui_chat_transcript (découverte, état runtime et lecture paginée de transcripts).
# 6.23.9.1: Orthogonalité stricte des modes de destination des automatisations (new_chat, existing_chat, persistent_chat), inférence automatique sur target_chat_id, sentinelle target_chat_id='reset' et factorisation des helpers.
# 6.23.1.1: Mise à niveau PAS de tool_advisor via compute_subagent_excluded_tools (exclusion fidèle allowed_tools dans hierarchical_domains_map) et gabarits TPL_ORCHESTRATOR_CONSULTANT_* (grade can_delegate, group_id, sandbox/<peer_sid>/).

# ECHO CONFIG NAME : ECHO Generalist Tools

import asyncio
import os
import re
import sys
import json
import inspect
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field
from typing import Any, Literal, Optional

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool, paginate_lines_window, wrap_tool_output
from echo_events import EchoEvents
from echo_constants import (
    ECHO_CODEX_MAX_READ_BYTES,
    ECHO_MAX_WAIT_TIMER,
    ECHO_SUBAGENT_CONTEXT,
    get_generation_config,
    get_headless_block_reason,
    OWUI_ORCHESTRATOR_MODEL,
)
from echo_paths import get_echo_session_path, sanitize_echo_id
from echo_chats import list_user_chats, read_user_chat_transcript
from echo_gemini_client import EchoGeminiClient, describe_empty_llm_response
from echo_prompts import (
    SYS_ORCHESTRATOR_CONSULTANT,
    TPL_ORCHESTRATOR_CONSULTANT_CALLER_MAIN,
    TPL_ORCHESTRATOR_CONSULTANT_CALLER_SUBAGENT,
    TPL_ORCHESTRATOR_CONSULTANT_ROLE_LEAF,
    TPL_ORCHESTRATOR_CONSULTANT_ROLE_SUPERIOR,
)
from echo_tool_guides import EchoToolGuidesManager
try:
    from echo_window_manager import get_window_manager
except ImportError:
    get_window_manager = None

from open_webui.tools.builtin import (
    search_calendar_events as _owui_search,
    create_calendar_event as _owui_create_cal,
    update_calendar_event as _owui_update_cal,
    delete_calendar_event as _owui_delete_cal,
    create_automation as _owui_create_auto,
    update_automation as _owui_update_auto,
    toggle_automation as _owui_toggle_auto,
    delete_automation as _owui_delete_auto,
    TaskItem as UiTaskItem,
    create_tasks as _owui_create_tasks,
    update_task as _owui_update_task,
    _get_user_tz,
    _ns_to_dt,
)
from open_webui.models.folders import Folders, FolderForm
from open_webui.models.tags import Tags
from open_webui.models.chats import Chats, is_internal_chat
from open_webui.models.automations import Automations
from open_webui.models.calendar import CalendarEvents
from open_webui.utils.chat_id import is_saved_chat_id
import logging

log = logging.getLogger(__name__)

async def safe_owui_call(func, *args, **kwargs):
    """Proxy sécurisé pour l'exécution et l'encapsulation ECHO des fonctions natives Open WebUI."""
    sig = inspect.signature(func)
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    filtered_kwargs = kwargs if has_var_keyword else {k: v for k, v in kwargs.items() if k in sig.parameters}
    
    try:
        res = await func(*args, **filtered_kwargs)
        status_dict = {"status": "success"}
        text_output = str(res)
        
        if isinstance(res, str):
            try:
                parsed = json.loads(res)
                if isinstance(parsed, dict):
                    if "error" in parsed:
                        status_dict = {"status": "error", "message": parsed["error"]}
                    elif "status" in parsed:
                        status_dict = {"status": str(parsed["status"])}
            except Exception:
                pass
                
        return wrap_tool_output(
            text=text_output, 
            status=status_dict, 
        )
    except Exception as e:
        return wrap_tool_output(
            text=f"Exception interne de l'outil : {str(e)}", 
            status={"status": "error", "message": str(e)}, 
        )

class Tools:
    class Valves(BaseModel):
        MAX_TIMER: int = Field(
            default=ECHO_MAX_WAIT_TIMER,
            description="Durée maximale autorisée pour le timer (en secondes)."
        )

    def __init__(self):
        self.valves = self.Valves()

    @echo_tool
    async def wait_timer(
        self,
        seconds: int,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        __metadata__: dict = {},
    ) -> str:
        """Suspend l'exécution de manière asynchrone pendant `seconds` secondes (max 180s, 1 appel max par tour)."""
        uid = str((__user__ or {}).get("id") or "")
        cid = str((__metadata__ or {}).get("chat_id") or "")
        events = EchoEvents(__event_emitter__, __event_call__, user_id=uid, chat_id=cid)

        # 1. Validation & Clamp de sécurité
        try:
            sec_val = int(seconds)
        except ValueError:
            sec_val = 10

        max_t = int(self.valves.MAX_TIMER)
        if sec_val < 1:
            sec_val = 1
        if sec_val > max_t:
            sec_val = max_t

        await events.status(f"⏱️ Démarrage d'un timer de {sec_val} secondes...")

        # 2. Diffusion vers le HUD natif Svelte via le bus Window Manager
        wm = get_window_manager() if get_window_manager is not None else None
        if wm is not None:
            await wm.broadcast_wait_timer(uid, cid, sec_val, active=True)

        # 3. Blocage Backend (Attente réelle)
        try:
            await asyncio.sleep(sec_val)
        finally:
            if wm is not None:
                await wm.broadcast_wait_timer(uid, cid, 0, active=False)

        await events.status("⏱️ Timer terminé.", done=True)
        return f"Le timer de {sec_val} secondes est terminé avec succès."

    @echo_tool
    async def ask_user_input(
        self,
        question: str = "",
        input_type: str = "text",
        options: Optional[list[Any]] = None,
        multi_select: bool = False,
        timeout_seconds: int = 300,
        questions: Optional[list[dict]] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Affiche une carte interactive Svelte native à l'Utilisateur pour saisir une réponse (`text`, choix unique ou `multi_select`), confirmer (`confirm`) ou répondre à un questionnaire multi-questions (`questions`)."""
        if not __user__:
            return wrap_tool_output(
                text=json.dumps({"status": "error", "message": "Contexte utilisateur manquant."}, ensure_ascii=False),
                status={"status": "error", "error_code": "missing_user"},
            )

        block_reason = get_headless_block_reason(__metadata__)
        if block_reason:
            return wrap_tool_output(
                text=f"Erreur : {block_reason}",
                status={"status": "error", "error_code": "headless_forbidden"},
            )

        if __event_call__ is None:
            return wrap_tool_output(
                text=json.dumps({"status": "error", "message": "Session navigateur interactive indisponible (__event_call__ manquant)."}, ensure_ascii=False),
                status={"status": "error", "error_code": "no_browser_session"},
            )

        def _normalize_options(raw_opts: Optional[list[Any]]) -> list[dict]:
            norm = []
            for opt in raw_opts or []:
                if isinstance(opt, str):
                    lbl = opt.strip()
                    if lbl:
                        norm.append({"label": lbl, "description": ""})
                elif isinstance(opt, dict):
                    lbl = str(opt.get("label") or "").strip()
                    if not lbl:
                        continue
                    item = {"label": lbl, "description": str(opt.get("description") or "").strip()}
                    if isinstance(opt.get("recommended"), bool):
                        item["recommended"] = opt["recommended"]
                    norm.append(item)
            return norm

        if isinstance(questions, list) and len(questions) > 0:
            normalized_questions = []
            seen_ids = set()
            for idx, q in enumerate(questions[:5]):
                if not isinstance(q, dict):
                    continue
                q_text = str(q.get("question") or "").strip()
                if not q_text:
                    continue
                qid = str(q.get("id") or f"q_{idx + 1}").strip()
                if qid in seen_ids:
                    qid = f"{qid}_{idx + 1}"
                seen_ids.add(qid)
                normalized_questions.append({
                    "id": qid,
                    "header": str(q.get("header") or f"Question {idx + 1}").strip(),
                    "question": q_text,
                    "options": _normalize_options(q.get("options")),
                    "multi_select": bool(q.get("multi_select", False)),
                    "allow_other": bool(q.get("allow_other", True)),
                    "show_recommended": bool(q.get("show_recommended", False)),
                })
            if not normalized_questions:
                return wrap_tool_output(
                    text=json.dumps({"status": "error", "message": "Aucune question valide fournie dans `questions`."}, ensure_ascii=False),
                    status={"status": "error", "error_code": "invalid_questions"},
                )
            global_allow_other = True
        else:
            q_text = str(question or "").strip()
            if not q_text:
                return wrap_tool_output(
                    text=json.dumps({"status": "error", "message": "Le paramètre `question` est requis."}, ensure_ascii=False),
                    status={"status": "error", "error_code": "missing_question"},
                )
            if input_type == "confirm":
                normalized_questions = [{
                    "id": "confirm",
                    "header": "Confirmation",
                    "question": q_text,
                    "options": [{"label": "Oui", "description": ""}, {"label": "Non", "description": ""}],
                    "allow_other": False,
                    "show_recommended": False,
                }]
                global_allow_other = False
            else:
                normalized_questions = [{
                    "id": "answer",
                    "header": "Question",
                    "question": q_text,
                    "options": _normalize_options(options),
                    "multi_select": bool(multi_select),
                    "allow_other": True,
                    "show_recommended": False,
                }]
                global_allow_other = True

        timeout_sec = max(10, min(int(timeout_seconds or 300), 600))
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"En attente d'une réponse utilisateur ({timeout_sec}s)...")

        response = await events.call(
            "request:user_input",
            {
                "questions": normalized_questions,
                "allow_other": global_allow_other,
                "timeout_ms": timeout_sec * 1000,
            },
        )

        if not isinstance(response, dict) or response.get("status") != "answered":
            await events.status("Saisie annulée ou délai expiré.", done=True)
            return wrap_tool_output(
                text=json.dumps(
                    {
                        "status": "cancelled",
                        "message": "L'utilisateur a annulé la saisie ou le délai imparti est expiré.",
                        "user_input": False if (input_type == "confirm" and not questions) else None,
                    },
                    ensure_ascii=False,
                ),
                status={"status": "cancelled"},
            )

        answers = response.get("answers") or {}
        extracted_map = {}
        for qid, ans in answers.items():
            if isinstance(ans, dict):
                if ans.get("type") == "multi":
                    selected = [str(lbl).strip() for lbl in (ans.get("labels") or []) if str(lbl).strip()]
                    other_val = str(ans.get("other_text") or "").strip()
                    if other_val:
                        selected.append(other_val)
                    extracted_map[qid] = selected
                elif ans.get("type") == "other":
                    extracted_map[qid] = ans.get("text", "")
                else:
                    extracted_map[qid] = ans.get("label", "")

        await events.status("Réponse utilisateur reçue.", done=True)

        if questions:
            return wrap_tool_output(
                text=json.dumps({"status": "success", "answers": extracted_map}, ensure_ascii=False),
                status={"status": "success"},
            )

        if input_type == "confirm":
            if str(extracted_map.get("confirm", "")).strip().lower() in ("oui", "yes", "true", "1"):
                return wrap_tool_output(
                    text=json.dumps({"status": "success", "user_input": True}, ensure_ascii=False),
                    status={"status": "success"},
                )
            return wrap_tool_output(
                text=json.dumps({"status": "cancelled", "message": "L'utilisateur a répondu Non.", "user_input": False}, ensure_ascii=False),
                status={"status": "cancelled"},
            )

        return wrap_tool_output(
            text=json.dumps({"status": "success", "user_input": extracted_map.get("answer", "")}, ensure_ascii=False),
            status={"status": "success"},
        )

    @staticmethod
    def _normalize_id_list(raw_ids: Any) -> list[str]:
        """Normalise une liste d'identifiants (list[str], tableau JSON, CSV ou chaîne unitaire)."""
        if raw_ids is None:
            return []
        if isinstance(raw_ids, (list, tuple, set)):
            return [str(item).strip() for item in raw_ids if item is not None and str(item).strip()]
        if isinstance(raw_ids, str):
            raw_str = raw_ids.strip()
            if not raw_str:
                return []
            if raw_str.startswith("["):
                try:
                    parsed = json.loads(raw_str)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if item is not None and str(item).strip()]
                except Exception:
                    pass
            return [part.strip() for part in raw_str.split(",") if part.strip()]
        return []

    @staticmethod
    def _ui_tool_error(msg: str, error_code: str = "invalid_arguments") -> dict:
        """Formate une erreur structurée normalisée pour les outils UI."""
        return wrap_tool_output(
            text=json.dumps({"error": msg}, ensure_ascii=False),
            status={"status": "error", "error_code": error_code, "message": msg},
        )

    @staticmethod
    def _parse_calendar_dt_ns(dt_str: str, tz_obj: Any) -> int:
        """Convertit une chaîne ISO 8601 ou 'YYYY-MM-DD HH:MM' en nanosecondes epoch UTC."""
        if isinstance(tz_obj, str):
            try:
                eff_tz: tzinfo = ZoneInfo(tz_obj)
            except Exception:
                eff_tz = timezone.utc
        elif tz_obj is None:
            eff_tz = timezone.utc
        elif isinstance(tz_obj, tzinfo) or hasattr(tz_obj, "utcoffset"):
            eff_tz = tz_obj
        else:
            eff_tz = timezone.utc

        dt = datetime.fromisoformat(dt_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=eff_tz)
        return int(dt.timestamp() * 1_000) * 1_000_000

    @classmethod
    def _validate_calendar_bounds(
        cls,
        start: Optional[str],
        end: Optional[str],
        tz: Any,
        existing_start_ns: Optional[int] = None,
        existing_end_ns: Optional[int] = None,
    ) -> Optional[dict]:
        """Valide le format des bornes `start`/`end` et leur cohérence chronologique."""
        start_ns: Optional[int] = None
        if isinstance(start, str):
            if start.strip():
                try:
                    start_ns = cls._parse_calendar_dt_ns(start, tz)
                except (ValueError, TypeError) as e:
                    return cls._ui_tool_error(
                        f"Format de date `start` invalide : {e}. Utiliser 'YYYY-MM-DD HH:MM' ou ISO 8601."
                    )
        elif start is not None:
            return cls._ui_tool_error(
                "Format de date `start` invalide : chaîne attendue. Utiliser 'YYYY-MM-DD HH:MM' ou ISO 8601."
            )

        end_ns: Optional[int] = None
        if isinstance(end, str):
            if end.strip():
                try:
                    end_ns = cls._parse_calendar_dt_ns(end, tz)
                except (ValueError, TypeError) as e:
                    return cls._ui_tool_error(
                        f"Format de date `end` invalide : {e}. Utiliser 'YYYY-MM-DD HH:MM' ou ISO 8601."
                    )
        elif end is not None:
            return cls._ui_tool_error(
                "Format de date `end` invalide : chaîne attendue. Utiliser 'YYYY-MM-DD HH:MM' ou ISO 8601."
            )

        eff_start = start_ns if start_ns is not None else existing_start_ns
        eff_end = end_ns if end_ns is not None else existing_end_ns
        if eff_start is not None and eff_end is not None and eff_end < eff_start:
            return cls._ui_tool_error(
                "Incohérence chronologique : la date de fin (`end`) doit être supérieure ou égale à la date de début (`start`)."
            )
        return None

    @staticmethod
    def _aggregate_batch_results(item_ids: list[str], raw_results: list[dict], id_field: str = "id") -> dict:
        """Agrège les retours d'une exécution batch multi-identifiants en réponse unique structurée."""
        succeeded: list[str] = []
        failed: list[dict] = []
        items_out: list[dict] = []
        for item_id, res in zip(item_ids, raw_results):
            st_dict = res.get("status") if isinstance(res, dict) and isinstance(res.get("status"), dict) else {}
            st_val = str(st_dict.get("status") or "success")
            text_val = res.get("text", "") if isinstance(res, dict) else str(res)
            entry = {id_field: item_id, "status": st_val, "result": text_val}
            items_out.append(entry)
            if st_val == "error":
                failed.append({id_field: item_id, "error": st_dict.get("message") or text_val})
            else:
                succeeded.append(item_id)

        overall_status = "success" if not failed else ("error" if not succeeded else "warning")
        payload = {
            "status": overall_status,
            "total": len(item_ids),
            "succeeded_ids": succeeded,
            "failed": failed,
            "results": items_out,
        }
        return wrap_tool_output(
            text=json.dumps(payload, ensure_ascii=False),
            status={
                "status": overall_status,
                "succeeded_count": len(succeeded),
                "failed_count": len(failed),
            },
        )

    @echo_tool
    async def manage_ui_calendar(
        self,
        action: Literal["list", "read", "create", "update", "delete"] = "list",
        event_ids: Optional[list[str]] = None,
        query: str = "",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 30,
        title: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        all_day: Optional[bool] = None,
        is_cancelled: Optional[bool] = None,
        reminder_minutes: Optional[int] = None,
        __request__: Any = None,
        __user__: dict = {},
        __metadata__: dict = {},
    ) -> dict | str:
        """Gère l'agenda de l'Interface Utilisateur selon `action` (`list`, `read`, `create`, `update`, `delete`)."""
        if action not in ("list", "read", "create", "update", "delete"):
            return self._ui_tool_error(f"Action calendrier invalide : '{action}'. Valeurs autorisées : list, read, create, update, delete.")

        if action == "list":
            if (
                event_ids is not None
                or title is not None
                or description is not None
                or location is not None
                or all_day is not None
                or is_cancelled is not None
                or reminder_minutes is not None
            ):
                return self._ui_tool_error("Paramètre(s) hors-contrat pour action='list' : seuls `query`, `start`, `end` et `limit` sont autorisés.")
            if start is not None or end is not None:
                bounds_err = self._validate_calendar_bounds(start, end, _get_user_tz(__user__))
                if bounds_err is not None:
                    return bounds_err
            return await safe_owui_call(
                _owui_search,
                query=query,
                start=start,
                end=end,
                count=limit,
                __request__=__request__,
                __user__=__user__,
                __metadata__=__metadata__,
            )

        if action == "read":
            if (
                query != ""
                or limit != 30
                or start is not None
                or end is not None
                or title is not None
                or description is not None
                or location is not None
                or all_day is not None
                or is_cancelled is not None
                or reminder_minutes is not None
            ):
                return self._ui_tool_error("Paramètre(s) hors-contrat pour action='read' : seul `event_ids` est autorisé.")

            user_id = (__user__ or {}).get("id")
            if not user_id:
                return self._ui_tool_error("Contexte utilisateur manquant.", error_code="missing_user")

            clean_ids = self._normalize_id_list(event_ids)
            if not clean_ids:
                return self._ui_tool_error("Aucun identifiant d'évènement valide fourni dans `event_ids`.")

            try:
                tz = _get_user_tz(__user__)
                tasks = [CalendarEvents.get_event_by_id(eid) for eid in clean_ids]
                raw_results = await asyncio.gather(*tasks, return_exceptions=True)

                events = []
                not_found = []
                for eid, res in zip(clean_ids, raw_results):
                    if isinstance(res, Exception) or res is None or res.user_id != user_id:
                        not_found.append(eid)
                        continue
                    alert_minutes = 10
                    if res.meta and "alert_minutes" in res.meta:
                        alert_minutes = res.meta["alert_minutes"]
                    events.append({
                        "id": res.id,
                        "calendar_id": res.calendar_id,
                        "title": res.title,
                        "description": res.description or "",
                        "start": _ns_to_dt(res.start_at, tz) if res.start_at else None,
                        "end": _ns_to_dt(res.end_at, tz) if res.end_at else None,
                        "all_day": res.all_day,
                        "rrule": res.rrule or "",
                        "location": res.location or "",
                        "color": res.color or "",
                        "reminder_minutes": alert_minutes,
                        "is_cancelled": res.is_cancelled,
                        "attendees": [a.model_dump() if hasattr(a, "model_dump") else a for a in (res.attendees or [])],
                        "created_at": res.created_at,
                        "updated_at": res.updated_at,
                    })

                payload = {"events": events, "total_found": len(events)}
                if not_found:
                    payload["not_found"] = not_found
                return json.dumps(payload, ensure_ascii=False)
            except Exception as e:
                return self._ui_tool_error(str(e), error_code="internal_error")

        if action == "create":
            if event_ids is not None or query != "" or limit != 30 or is_cancelled is not None:
                return self._ui_tool_error("Paramètre(s) hors-contrat pour action='create' : `event_ids`, `query`, `limit` et `is_cancelled` sont interdits.")
            if not isinstance(title, str) or not title.strip() or not isinstance(start, str) or not start.strip():
                return self._ui_tool_error("Les paramètres `title` et `start` non vides sont requis pour action='create'.")
            bounds_err = self._validate_calendar_bounds(start, end, _get_user_tz(__user__))
            if bounds_err is not None:
                return bounds_err
            return await safe_owui_call(
                _owui_create_cal,
                title=title.strip(),
                description=description,
                start=start.strip(),
                end=end,
                all_day=bool(all_day) if all_day is not None else False,
                location=location,
                reminder_minutes=reminder_minutes,
                __request__=__request__,
                __user__=__user__,
                __metadata__=__metadata__,
            )

        if action == "update":
            if query != "" or limit != 30:
                return self._ui_tool_error("Paramètre(s) hors-contrat pour action='update' : `query` et `limit` sont interdits.")
            clean_ids = self._normalize_id_list(event_ids)
            if len(clean_ids) != 1:
                return self._ui_tool_error("action='update' exige exactement 1 identifiant dans `event_ids`.")
            if start is not None or end is not None:
                existing_ev = None
                if hasattr(CalendarEvents, "get_event_by_id"):
                    try:
                        existing_ev = await CalendarEvents.get_event_by_id(clean_ids[0])
                    except Exception:
                        existing_ev = None
                existing_start_ns = getattr(existing_ev, "start_at", None)
                existing_end_ns = getattr(existing_ev, "end_at", None)
                bounds_err = self._validate_calendar_bounds(
                    start,
                    end,
                    _get_user_tz(__user__),
                    existing_start_ns=existing_start_ns,
                    existing_end_ns=existing_end_ns,
                )
                if bounds_err is not None:
                    return bounds_err
            return await safe_owui_call(
                _owui_update_cal,
                event_id=clean_ids[0],
                title=title,
                description=description,
                start=start,
                end=end,
                all_day=all_day,
                location=location,
                is_cancelled=is_cancelled,
                reminder_minutes=reminder_minutes,
                __request__=__request__,
                __user__=__user__,
                __metadata__=__metadata__,
            )

        # action == "delete"
        if (
            query != ""
            or limit != 30
            or start is not None
            or end is not None
            or title is not None
            or description is not None
            or location is not None
            or all_day is not None
            or is_cancelled is not None
            or reminder_minutes is not None
        ):
            return self._ui_tool_error("Paramètre(s) hors-contrat pour action='delete' : seul `event_ids` est autorisé.")
        clean_ids = self._normalize_id_list(event_ids)
        if not clean_ids:
            return self._ui_tool_error("Aucun identifiant d'évènement valide fourni dans `event_ids`.")
        if len(clean_ids) == 1:
            return await safe_owui_call(
                _owui_delete_cal,
                event_id=clean_ids[0],
                __request__=__request__,
                __user__=__user__,
                __metadata__=__metadata__,
            )
        results = await asyncio.gather(*(
            safe_owui_call(
                _owui_delete_cal,
                event_id=eid,
                __request__=__request__,
                __user__=__user__,
                __metadata__=__metadata__,
            )
            for eid in clean_ids
        ))
        return self._aggregate_batch_results(clean_ids, results, id_field="event_id")

    @classmethod
    def _automation_error(cls, msg: str) -> dict:
        return cls._ui_tool_error(msg, error_code="automation_error")

    @classmethod
    def _resolve_current_chat_id(cls, metadata: Optional[dict]) -> tuple[Optional[str], Optional[dict]]:
        cid = (metadata or {}).get("chat_id")
        if not cid or str(cid).startswith("local:"):
            return None, cls._automation_error(
                "Impossible de cibler la conversation courante : chat_id absent ou temporaire ('local:')."
            )
        return str(cid), None

    @staticmethod
    async def _chat_exists_for_user(user_id: str, chat_id: Optional[str]) -> bool:
        cleaned = (chat_id or "").strip()
        if not cleaned or not is_saved_chat_id(cleaned):
            return False
        chat = await Chats.get_chat_by_id_and_user_id(cleaned, user_id)
        return bool(chat and not is_internal_chat(chat.meta))

    @staticmethod
    async def _resolve_or_create_folder_id(user_id: str, folder_name: Optional[str]) -> Optional[str]:
        if not folder_name:
            return None
        folders = await Folders.get_folders_by_user_id(user_id) if hasattr(Folders, "get_folders_by_user_id") else []
        match = next((f for f in folders if f.name == folder_name), None)
        if match:
            return match.id
        try:
            new_folder = await Folders.insert_new_folder(user_id, FolderForm(name=folder_name))
            return new_folder.id
        except Exception as e:
            log.error(f"ECHO: Failed to create target folder '{folder_name}' - {e}")
            return None

    @staticmethod
    def _with_orchestrator_model_metadata(metadata: Optional[dict]) -> dict:
        call_metadata = dict(metadata or {})
        call_metadata["model_id"] = OWUI_ORCHESTRATOR_MODEL
        if isinstance(call_metadata.get("model"), dict):
            call_metadata["model"] = dict(call_metadata["model"], id=OWUI_ORCHESTRATOR_MODEL)
        else:
            call_metadata["model"] = {"id": OWUI_ORCHESTRATOR_MODEL}
        return call_metadata

    @echo_tool
    async def manage_ui_automations(
        self,
        action: Literal["list", "read", "create", "update", "toggle", "delete"] = "list",
        automation_ids: Optional[list[str]] = None,
        name: Optional[str] = None,
        prompt: Optional[str] = None,
        rrule: Optional[str] = None,
        target_folder_name: Optional[str] = None,
        target_mode: Optional[Literal["new_chat", "existing_chat", "persistent_chat"]] = None,
        target_chat_id: Optional[str] = None,
        query: str = "",
        limit: int = 30,
        __request__: Any = None,
        __user__: dict = {},
        __metadata__: dict = {},
    ) -> dict | str:
        """Gère les tâches de fond planifiées (CRON) de l'Interface Utilisateur selon `action` (`list`, `read`, `create`, `update`, `toggle`, `delete`)."""
        if action not in ("list", "read", "create", "update", "toggle", "delete"):
            return self._ui_tool_error(
                f"Action automatisation invalide : '{action}'. Valeurs autorisées : list, read, create, update, toggle, delete."
            )

        user_id = (__user__ or {}).get("id")
        if not user_id:
            return self._ui_tool_error("Contexte utilisateur manquant.", error_code="missing_user")

        if action == "list":
            if (
                automation_ids is not None
                or name is not None
                or prompt is not None
                or rrule is not None
                or target_folder_name is not None
                or target_mode is not None
                or target_chat_id is not None
            ):
                return self._ui_tool_error(
                    "Paramètre(s) hors-contrat pour action='list' : seuls `query` et `limit` sont autorisés."
                )
            try:
                result = await Automations.search_automations(user_id=user_id, query=query, limit=limit)
                items = [
                    {
                        "id": item.id,
                        "name": item.name,
                        "folder_id": item.folder_id,
                        "rrule": item.data.get("rrule", "") if item.data else "",
                        "target": item.data.get("target") if item.data else None,
                        "is_active": item.is_active,
                        "next_run_at": item.next_run_at,
                    }
                    for item in result.items
                ]
                return json.dumps(items, ensure_ascii=False)
            except Exception as e:
                return self._ui_tool_error(str(e), error_code="internal_error")

        if action == "read":
            if (
                name is not None
                or prompt is not None
                or rrule is not None
                or target_folder_name is not None
                or target_mode is not None
                or target_chat_id is not None
                or query != ""
                or limit != 30
            ):
                return self._ui_tool_error(
                    "Paramètre(s) hors-contrat pour action='read' : seul `automation_ids` est autorisé."
                )
            clean_ids = self._normalize_id_list(automation_ids)
            if not clean_ids:
                return self._ui_tool_error("Aucun identifiant d'automatisation valide fourni dans `automation_ids`.")
            try:
                tasks = [Automations.get_by_id(aid) for aid in clean_ids]
                raw_results = await asyncio.gather(*tasks, return_exceptions=True)

                automations = []
                not_found = []
                for aid, res in zip(clean_ids, raw_results):
                    if isinstance(res, Exception) or res is None or res.user_id != user_id:
                        not_found.append(aid)
                        continue
                    automations.append({
                        "id": res.id,
                        "name": res.name,
                        "folder_id": res.folder_id,
                        "prompt": res.data.get("prompt", "") if res.data else "",
                        "model_id": res.data.get("model_id", "") if res.data else "",
                        "rrule": res.data.get("rrule", "") if res.data else "",
                        "target": res.data.get("target") if res.data else None,
                        "is_active": res.is_active,
                        "last_run_at": res.last_run_at,
                        "next_run_at": res.next_run_at,
                        "created_at": res.created_at,
                        "updated_at": res.updated_at,
                    })

                payload = {"automations": automations, "total_found": len(automations)}
                if not_found:
                    payload["not_found"] = not_found
                return json.dumps(payload, ensure_ascii=False)
            except Exception as e:
                return self._ui_tool_error(str(e), error_code="internal_error")

        if action == "create":
            if automation_ids is not None or query != "" or limit != 30:
                return self._ui_tool_error(
                    "Paramètre(s) hors-contrat pour action='create' : `automation_ids`, `query` et `limit` sont interdits."
                )
            if (
                not isinstance(name, str) or not name.strip()
                or not isinstance(prompt, str) or not prompt.strip()
                or not isinstance(rrule, str) or not rrule.strip()
            ):
                return self._ui_tool_error(
                    "Les paramètres `name`, `prompt` et `rrule` non vides sont requis pour action='create'."
                )

            eff_mode = target_mode if target_mode is not None else "new_chat"
            eff_folder_name = target_folder_name if target_folder_name is not None else "Automations"
            raw_chat_id = target_chat_id.strip() if isinstance(target_chat_id, str) and target_chat_id.strip() else None
            resolved_chat_id = None

            if raw_chat_id == "reset":
                if eff_mode != "persistent_chat":
                    return self._automation_error(
                        "Incohérence de ciblage : target_chat_id='reset' est réservé à la réinitialisation d'une automatisation existante en target_mode='persistent_chat'."
                    )
                effective_type = "persistent_chat"
            elif eff_mode == "persistent_chat":
                if raw_chat_id is not None:
                    return self._automation_error(
                        "Incohérence de ciblage : target_mode='persistent_chat' crée automatiquement sa conversation dédiée et n'accepte pas target_chat_id (utiliser target_mode='existing_chat' pour cibler une conversation existante)."
                    )
                effective_type = "persistent_chat"
            elif eff_mode == "existing_chat" or (raw_chat_id is not None and eff_mode == "new_chat"):
                if not raw_chat_id:
                    return self._automation_error(
                        "target_chat_id ('current' ou UUID) est requis lorsque target_mode='existing_chat'."
                    )
                if raw_chat_id == "current":
                    resolved_chat_id, err = self._resolve_current_chat_id(__metadata__)
                    if err:
                        return err
                else:
                    resolved_chat_id = raw_chat_id
                effective_type = "existing_chat"
            else:
                effective_type = "chat"

            folder_id = (
                None
                if effective_type == "existing_chat"
                else await self._resolve_or_create_folder_id(user_id, eff_folder_name)
            )
            call_metadata = self._with_orchestrator_model_metadata(__metadata__)

            return await safe_owui_call(
                _owui_create_auto,
                name=name.strip(),
                prompt=prompt.strip(),
                rrule=rrule.strip(),
                folder_id=folder_id,
                model_id=OWUI_ORCHESTRATOR_MODEL,
                target_type=effective_type,
                target_chat_id=resolved_chat_id,
                __request__=__request__,
                __user__=__user__,
                __metadata__=call_metadata,
            )

        if action == "update":
            if query != "" or limit != 30:
                return self._ui_tool_error(
                    "Paramètre(s) hors-contrat pour action='update' : `query` et `limit` sont interdits."
                )
            clean_ids = self._normalize_id_list(automation_ids)
            if len(clean_ids) != 1:
                return self._ui_tool_error("action='update' exige exactement 1 identifiant dans `automation_ids`.")
            aid = clean_ids[0]

            automation = await Automations.get_by_id(aid)
            if not automation:
                return self._automation_error("Automation not found")
            if automation.user_id != user_id:
                return self._automation_error("Access denied")

            existing_target = (automation.data or {}).get("target") or {}
            old_type = existing_target.get("type") or "chat"
            old_chat_id = existing_target.get("chat_id")
            raw_chat_id = target_chat_id.strip() if isinstance(target_chat_id, str) and target_chat_id.strip() else None

            mapped_target_type = None
            extra_target_kwargs = {}

            if raw_chat_id == "reset":
                if target_mode in ("new_chat", "existing_chat"):
                    return self._automation_error(
                        "Incohérence de ciblage : target_chat_id='reset' est réservé à target_mode='persistent_chat'."
                    )
                mapped_target_type = "persistent_chat"
                extra_target_kwargs = {"target_type": "persistent_chat", "target_chat_id": None}
            elif raw_chat_id is not None:
                if target_mode in ("new_chat", "persistent_chat"):
                    return self._automation_error(
                        "Incohérence de ciblage : target_chat_id ne peut être utilisé qu'avec target_mode='existing_chat' (ou target_mode omis)."
                    )
                if raw_chat_id == "current":
                    resolved_chat_id, err = self._resolve_current_chat_id(__metadata__)
                    if err:
                        return err
                else:
                    resolved_chat_id = raw_chat_id
                mapped_target_type = "existing_chat"
                extra_target_kwargs = {"target_type": "existing_chat", "target_chat_id": resolved_chat_id}
            elif target_mode == "new_chat":
                mapped_target_type = "chat"
                extra_target_kwargs = {"target_type": "chat", "target_chat_id": None}
            elif target_mode == "existing_chat":
                if old_type == "existing_chat" and await self._chat_exists_for_user(user_id, old_chat_id):
                    mapped_target_type = "existing_chat"
                    extra_target_kwargs = {"target_type": "existing_chat"}
                else:
                    return self._automation_error(
                        "target_chat_id ('current' ou UUID) est requis pour cibler une conversation existante (target_mode='existing_chat')."
                    )
            elif target_mode == "persistent_chat":
                mapped_target_type = "persistent_chat"
                if old_type == "persistent_chat" and old_chat_id and await self._chat_exists_for_user(user_id, old_chat_id):
                    extra_target_kwargs = {"target_type": "persistent_chat"}
                else:
                    extra_target_kwargs = {"target_type": "persistent_chat", "target_chat_id": None}

            effective_final_type = mapped_target_type or old_type
            folder_id = ""
            if target_folder_name is not None and effective_final_type != "existing_chat":
                resolved_fid = await self._resolve_or_create_folder_id(user_id, target_folder_name)
                folder_id = resolved_fid if resolved_fid is not None else ""

            call_metadata = self._with_orchestrator_model_metadata(__metadata__)
            return await safe_owui_call(
                _owui_update_auto,
                automation_id=aid,
                name=name,
                prompt=prompt,
                rrule=rrule,
                model_id=OWUI_ORCHESTRATOR_MODEL,
                folder_id=folder_id,
                **extra_target_kwargs,
                __request__=__request__,
                __user__=__user__,
                __metadata__=call_metadata,
            )

        # actions "toggle" et "delete" (1..N IDs)
        if (
            name is not None
            or prompt is not None
            or rrule is not None
            or target_folder_name is not None
            or target_mode is not None
            or target_chat_id is not None
            or query != ""
            or limit != 30
        ):
            return self._ui_tool_error(
                f"Paramètre(s) hors-contrat pour action='{action}' : seul `automation_ids` est autorisé."
            )
        clean_ids = self._normalize_id_list(automation_ids)
        if not clean_ids:
            return self._ui_tool_error("Aucun identifiant d'automatisation valide fourni dans `automation_ids`.")

        target_fn = _owui_toggle_auto if action == "toggle" else _owui_delete_auto
        if len(clean_ids) == 1:
            extra_kw = {"__metadata__": __metadata__} if action == "delete" else {}
            return await safe_owui_call(
                target_fn,
                automation_id=clean_ids[0],
                __request__=__request__,
                __user__=__user__,
                **extra_kw,
            )
        results = await asyncio.gather(*(
            safe_owui_call(
                target_fn,
                automation_id=aid,
                __request__=__request__,
                __user__=__user__,
                **({"__metadata__": __metadata__} if action == "delete" else {}),
            )
            for aid in clean_ids
        ))
        return self._aggregate_batch_results(clean_ids, results, id_field="automation_id")

    @echo_tool
    async def list_ui_folders(self, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """Liste l'arborescence des dossiers de conversations de l'Interface Utilisateur (`id`, `name`)."""
        user_id = __user__.get("id")
        folders = await Folders.get_folders_by_user_id(user_id) if hasattr(Folders, 'get_folders_by_user_id') else []
        res = json.dumps([{"id": f.id, "name": f.name} for f in folders])
        return res

    @echo_tool
    async def list_ui_tags(
        self,
        global_scope: bool = False,
        chat_id: str = None,
        __user__: dict = {},
        __metadata__: dict = {}
    ) -> str:
        """Liste les étiquettes (tags) de la conversation en cours (`global_scope=False`) ou de l'ensemble des conversations existantes (`global_scope=True`)."""
        user_id = __user__.get("id")
        cid = chat_id or (__metadata__ or {}).get("chat_id")
        try:
            if global_scope:
                all_tags = await Tags.get_tags_by_user_id(user_id) if hasattr(Tags, "get_tags_by_user_id") else []
                tag_ids = [t.id for t in all_tags]
                counts = await Chats.count_chats_by_tag_ids_and_user_id(tag_ids, user_id) if hasattr(Chats, "count_chats_by_tag_ids_and_user_id") and tag_ids else {}
                data = {
                    "scope": "global",
                    "total_tags": len(all_tags),
                    "tags": [{"id": t.id, "name": t.name, "chat_count": counts.get(t.id, 0)} for t in all_tags]
                }
            else:
                if not cid:
                    return self._ui_tool_error("Contexte chat_id manquant pour la conversation en cours.", error_code="missing_chat_id")
                chat = await Chats.get_chat_by_id_and_user_id(cid, user_id) if hasattr(Chats, "get_chat_by_id_and_user_id") else None
                raw_tag_ids = (chat.meta or {}).get("tags", []) if chat else []
                tag_models = await Tags.get_tags_by_ids_and_user_id(raw_tag_ids, user_id) if hasattr(Tags, "get_tags_by_ids_and_user_id") and raw_tag_ids else []
                tag_dict = {t.id: t.name for t in tag_models}
                data = {
                    "scope": "local",
                    "chat_id": cid,
                    "tags": [{"id": tid, "name": tag_dict.get(tid, tid)} for tid in raw_tag_ids]
                }
            return json.dumps(data, ensure_ascii=False)
        except Exception as e:
            return self._ui_tool_error(str(e), error_code="internal_error")

    @echo_tool
    async def list_ui_chats(
        self,
        query: Optional[str] = None,
        status: Literal["all", "running", "pending_approval", "queued", "error", "idle"] = "all",
        folder_id: Optional[str] = None,
        tag: Optional[str] = None,
        include_archived: bool = False,
        include_current: bool = True,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        __request__: Any = None,
        __user__: dict = {},
        __metadata__: dict = {},
    ) -> str:
        """Liste et recherche les conversations existantes de l'Utilisateur avec leur état d'exécution temps réel (`running`, `pending_approval`, `queued`, `error`, `idle`)."""
        uid = (__user__ or {}).get("id")
        cid = (__metadata__ or {}).get("chat_id")
        redis = getattr(getattr(__request__, "app", None), "state", None)
        redis = getattr(redis, "redis", None) if redis is not None else None
        res = await list_user_chats(
            user_id=uid,
            current_chat_id=cid,
            query=query,
            status=status,
            folder_id=folder_id,
            tag=tag,
            include_archived=include_archived,
            include_current=include_current,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
            redis=redis,
        )
        if res.get("status") == "error":
            return wrap_tool_output(text=f"❌ {res.get('error')}", status=res)
        return json.dumps(res, ensure_ascii=False)

    @echo_tool
    async def read_ui_chat_transcript(
        self,
        chat_id: str,
        offset: int = 0,
        limit: int = 20,
        order: Literal["newest_first", "oldest_first"] = "newest_first",
        search_pattern: Optional[str] = None,
        include_reasoning: bool = False,
        include_tool_calls: bool = False,
        __request__: Any = None,
        __user__: dict = {},
        __metadata__: dict = {},
    ) -> str:
        """Lit de manière paginée et assainie le transcript de la branche active d'une conversation (`chat_id`), incluant le flux en cours si `running`."""
        uid = (__user__ or {}).get("id")
        cid = (__metadata__ or {}).get("chat_id")
        redis = getattr(getattr(__request__, "app", None), "state", None)
        redis = getattr(redis, "redis", None) if redis is not None else None
        res = await read_user_chat_transcript(
            user_id=uid,
            chat_id=chat_id,
            current_chat_id=cid,
            offset=offset,
            limit=limit,
            order=order,
            search_pattern=search_pattern,
            include_reasoning=include_reasoning,
            include_tool_calls=include_tool_calls,
            redis=redis,
        )
        if res.get("status") == "error":
            return wrap_tool_output(text=f"❌ {res.get('error')}", status=res)
        return json.dumps(res, ensure_ascii=False)

    @echo_tool
    async def manage_ui_tasks(
        self,
        action: Literal["create", "update"],
        tasks: Optional[list[UiTaskItem]] = None,
        task_id: Optional[str] = None,
        status: Optional[Literal["pending", "in_progress", "completed", "cancelled"]] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
    ) -> dict:
        """Crée (`create`) ou met à jour (`update`) la liste de tâches visibles en temps réel dans l'interface du chat principal."""
        if ECHO_SUBAGENT_CONTEXT.get().get("is_subagent"):
            return wrap_tool_output(
                text="Erreur : L'environnement d'exécution (Headless/Sous-agent) ne permet pas de modifier la checklist UI du chat principal.",
                status={"status": "error", "error_code": "headless_forbidden"},
            )
        if action not in ("create", "update"):
            return self._ui_tool_error(f"Action de tâches invalide : '{action}'. Valeurs autorisées : create, update.")

        chat_id = (__metadata__ or {}).get("chat_id")
        if action == "create":
            if task_id is not None or status is not None:
                return self._ui_tool_error("Paramètre(s) hors-contrat pour action='create' : `task_id` et `status` sont interdits.")
            if not tasks:
                return wrap_tool_output(
                    text="Erreur : Aucun item valide dans `tasks`. Chaque objet DOIT contenir une clé `content` non vide (ex: `[{\"content\": \"Étape 1\", \"status\": \"pending\"}]`).",
                    status={"status": "error", "error_code": "invalid_tasks_payload"},
                )
            normalized_tasks = []
            for item in tasks or []:
                if hasattr(item, "model_dump"):
                    d = item.model_dump(exclude_none=True)
                elif isinstance(item, str):
                    d = {"content": item}
                elif isinstance(item, dict):
                    d = dict(item)
                else:
                    continue
                content = str(d.get("content") or d.get("title") or d.get("label") or d.get("text") or d.get("name") or d.get("description") or "").strip()
                if not content:
                    continue
                norm_item = {"content": content}
                if d.get("id") is not None and str(d.get("id")).strip():
                    norm_item["id"] = str(d["id"]).strip()
                if d.get("status"):
                    norm_item["status"] = str(d["status"]).strip().lower()
                normalized_tasks.append(norm_item)
            if not normalized_tasks:
                return wrap_tool_output(
                    text="Erreur : Aucun item valide dans `tasks`. Chaque objet DOIT contenir une clé `content` non vide (ex: `[{\"content\": \"Étape 1\", \"status\": \"pending\"}]`).",
                    status={"status": "error", "error_code": "invalid_tasks_payload"},
                )
            return await safe_owui_call(_owui_create_tasks, tasks=normalized_tasks, __chat_id__=chat_id, __event_emitter__=__event_emitter__, __user__=__user__)

        # action == "update"
        if tasks is not None:
            return self._ui_tool_error("Paramètre hors-contrat pour action='update' : `tasks` est interdit.")
        if not isinstance(task_id, str) or not task_id.strip():
            return self._ui_tool_error("Le paramètre `task_id` non vide est requis pour action='update'.")
        if status is None or not isinstance(status, str) or not status.strip():
            return self._ui_tool_error("Le paramètre `status` ('pending', 'in_progress', 'completed', 'cancelled') est requis pour action='update'.")
        eff_status = status.strip().lower()
        if eff_status not in ("pending", "in_progress", "completed", "cancelled"):
            return self._ui_tool_error(f"Statut de tâche invalide : '{eff_status}'.")
        return await safe_owui_call(_owui_update_task, id=task_id.strip(), status=eff_status, __chat_id__=chat_id, __event_emitter__=__event_emitter__, __user__=__user__)

    @echo_tool
    async def tool_advisor(
        self,
        objective: str = Field(
            ...,
            description="Objectif technique précis, question sur l'usage d'un outil, aide sur l'interface ECHO-UI à fournir à L'Utilisateur, ou contexte d'erreur à diagnostiquer."
        ),
        target_tools: str = Field(
            default=None,
            description="Liste optionnelle de noms de fonctions ou de domaines séparés par des virgules (ex: 'edit_codex,open_codex_ui', 'echo_ui_guide')."
        ),
        error_context: str = Field(
            default=None,
            description="Message d'erreur ou comportement inattendu observé lors d'un appel d'outil précédent."
        ),
        __user__: dict = {},
        __chat_id__: str = "",
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Consulte l'Expert-Conseil Opérationnel ECHO (manuels complets des outils, paramètres exacts, séquences d'appels, diagnostic d'erreurs et guide d'utilisation de l'interface ECHO-UI pour assister L'Utilisateur). Transmis par défaut à tous les sous-agents."""
        if not __user__:
            return "Erreur : contexte OWUI manquant."

        if not isinstance(target_tools, str):
            target_tools = None
        if not isinstance(error_context, str):
            error_context = None

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status("🧠 Conseiller Outils : Analyse de la base de connaissances opérationnelle...")

        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id") or ""
        user_id = __user__.get("id", "system")

        # Introspection Dynamique : Extraction sécurisée du registre unifié
        from echo_subagent_hub import compute_subagent_excluded_tools, get_chat_tools
        _tools_dict = get_chat_tools(chat_id, __metadata__)
        all_chat_tool_names = set(_tools_dict.keys())

        from echo_constants import ECHO_SUBAGENT_CONTEXT
        sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
        is_subagent = bool(sub_ctx.get("is_subagent"))
        if is_subagent and "sub_tools_names" in sub_ctx:
            allowed = set(sub_ctx["sub_tools_names"])
            _tools_dict = {k: v for k, v in _tools_dict.items() if k in allowed}

        # Liste noire : Ne pas lister l'auto-référence au conseiller
        if is_subagent:
            blacklist = compute_subagent_excluded_tools(sub_ctx, all_chat_tool_names)
            excluded_for_index = blacklist - {"tool_advisor"}
            blocked_sorted = ", ".join(sorted(excluded_for_index)) or "aucun"
            sub_sid_label = sub_ctx.get("sub_sid") or "inconnu"
            group_label = sub_ctx.get("group_id") or "aucun"
            depth_val = int(sub_ctx.get("depth") or 1)
            role_tpl = (
                TPL_ORCHESTRATOR_CONSULTANT_ROLE_SUPERIOR
                if sub_ctx.get("can_delegate")
                else TPL_ORCHESTRATOR_CONSULTANT_ROLE_LEAF
            )
            role_clause = role_tpl.format(depth=depth_val, group_id=group_label)
            caller_context = TPL_ORCHESTRATOR_CONSULTANT_CALLER_SUBAGENT.format(
                sub_sid=sub_sid_label,
                role_clause=role_clause,
                blocked_tools=blocked_sorted,
            )
        else:
            blacklist = {"tool_advisor"}
            excluded_for_index = None
            caller_context = TPL_ORCHESTRATOR_CONSULTANT_CALLER_MAIN

        # Construction textuelle des signatures techniques disponibles
        tools_docs = []
        for t_name, t_spec in _tools_dict.items():
            if t_name in blacklist:
                continue
            spec = t_spec.get("spec", {})
            desc = spec.get("description", "Pas de description")
            params = spec.get("parameters", {}).get("properties", {})
            required = spec.get("parameters", {}).get("required", [])
            tools_docs.append(
                f"- `{t_name}` (requis: {required}, params: {list(params.keys())}) : {desc}"
            )

        tools_catalog = "\n".join(tools_docs) if tools_docs else "Catalogue dynamique non chargé (se référer aux manuels)."

        # Résolution de la Carte Hiérarchique et pré-injection déterministe des manuels .md
        hierarchical_index = EchoToolGuidesManager.get_hierarchical_index(
            excluded_tools=excluded_for_index
        )
        preloaded_guides = EchoToolGuidesManager.resolve_guides(
            objective=objective,
            target_tools=target_tools,
            error_context=error_context,
            exclude_auto_domains={"echo_ui_guide"} if is_subagent else None,
        )

        system_prompt = SYS_ORCHESTRATOR_CONSULTANT.format(
            caller_context=caller_context,
            hierarchical_index=hierarchical_index,
            tools_catalog=tools_catalog,
            preloaded_guides=preloaded_guides,
        )

        # Déclaration de l'outil interne `read_tool_guide` pour lecture complémentaire à la volée
        internal_tools = [
            {
                "functionDeclarations": [
                    {
                        "name": "read_tool_guide",
                        "description": "Lit le manuel opérationnel complet d'un ou plusieurs domaines (`domain_id` issus de <hierarchical_domains_map>) ou noms d'outils (`tool_name`).",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "targets": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                    "description": "Liste de domain_id (ex: ['codex_management']) ou de noms de fonctions (ex: ['edit_codex']).",
                                }
                            },
                            "required": ["targets"],
                        },
                    }
                ]
            }
        ]

        user_prompt = objective
        if target_tools:
            user_prompt += f"\n\n[Outils/Domaines ciblés : {target_tools}]"
        if error_context:
            user_prompt += f"\n\n[Contexte d'erreur : {error_context}]"

        contents = [{"role": "user", "parts": [{"text": user_prompt}]}]

        try:
            text_result = ""
            last_response = None
            # Boucle hybride bornée à 2 tours max (Tour 1: réponse directe ou read_tool_guide -> Tour 2: synthèse finale)
            for turn_idx in range(2):
                payload = {
                    "contents": contents,
                    "generationConfig": get_generation_config("MODEL_APOLLO", override_thinking="medium"),
                    "systemInstruction": {"role": "system", "parts": [{"text": system_prompt}]},
                    "tools": internal_tools,
                }
                if turn_idx > 0:
                    payload["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}

                response, _, _ = await EchoGeminiClient.call_cascade(
                    target_model_key="MODEL_APOLLO",
                    payload=payload,
                    user_id=user_id,
                    metadata=__metadata__,
                    events=events,
                    chat_id=chat_id,
                    include_thoughts=False,
                )
                if not response:
                    await events.status("❌ Erreur Conseiller Outils : Cascade épuisée", done=True)
                    return wrap_tool_output(
                        text="Erreur lors de la délégation au Conseiller Outils : Aucun modèle disponible (cascade épuisée).",
                        status={"status": "error", "message": "cascade exhausted"},
                    )
                last_response = response

                candidates = response.get("candidates", [])
                if not candidates or not candidates[0].get("content"):
                    break

                model_content = candidates[0]["content"]
                parts = model_content.get("parts", [])
                fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]

                if fn_calls and turn_idx == 0:
                    contents.append(model_content)
                    fn_responses_parts = []
                    for fc in fn_calls:
                        fc_name = fc.get("name", "")
                        fc_args = fc.get("args", {}) or {}
                        if fc_name == "read_tool_guide":
                            req_targets = fc_args.get("targets", [])
                            if isinstance(req_targets, str):
                                req_targets = [req_targets]
                            await events.status(f"📖 Conseiller Outils : Lecture du manuel {', '.join(req_targets)}...")
                            guide_content = EchoToolGuidesManager.format_guides_for_targets(req_targets)
                            fn_resp = {
                                "name": fc_name,
                                "response": {"result": guide_content},
                            }
                            if fc.get("id"):
                                fn_resp["id"] = fc["id"]
                            fn_responses_parts.append({"functionResponse": fn_resp})
                        else:
                            fn_resp = {
                                "name": fc_name,
                                "response": {"error": f"Outil interne inconnu : {fc_name}"},
                            }
                            if fc.get("id"):
                                fn_resp["id"] = fc["id"]
                            fn_responses_parts.append({"functionResponse": fn_resp})
                    contents.append({"role": "user", "parts": fn_responses_parts})
                    continue

                for p in parts:
                    if "text" in p and not p.get("thought"):
                        text_result += p["text"]
                break

            if not text_result.strip():
                diag = describe_empty_llm_response(last_response)
                await events.status("❌ Conseiller Outils : Réponse vide ou bloquée.", done=True)
                return wrap_tool_output(
                    text=f"Erreur lors de la délégation au Conseiller Outils : {diag['summary']}",
                    status={
                        "status": "error",
                        "error_code": diag.get("kind") or "empty_response",
                        "reason": diag.get("reason") or "EMPTY_RESPONSE",
                        "message": diag["summary"],
                    },
                )

            await events.status("✅ Conseiller Outils : Recommandation opérationnelle prête.", done=True)
            return f"Recommandation du Conseiller Outils :\n\n{text_result}"
        except Exception as e:
            await events.status("❌ Erreur Conseiller Outils", done=True)
            return wrap_tool_output(
                text=f"Erreur lors de la délégation au Conseiller Outils : {str(e)}",
                status={"status": "error", "message": str(e)},
            )

    # ==========================================================================
    # 5. EXPLOITATION DES SORTIES D'OUTILS DÉLESTÉES (DOMAINE tools_output)
    # ==========================================================================

    @echo_tool
    async def read_tool_output(
        self,
        output_id: str,
        start_line: int = 1,
        end_line: Optional[int] = None,
        search_pattern: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
    ) -> dict:
        """Lit une plage de lignes (jusqu'à 800 lignes / 45 Ko) ou recherche un motif regex (`search_pattern`) dans une sortie d'outil délestée (`output_id`) du domaine `tools_output`."""
        uid = (__user__ or {}).get("id")
        cid = (__metadata__ or {}).get("chat_id")
        if not uid or not cid:
            return wrap_tool_output(
                text="❌ Contexte utilisateur ou chat_id manquant.",
                status={"status": "error", "error_code": "missing_context"},
            )

        raw_id = (output_id or "").strip()
        if raw_id.endswith(".txt"):
            raw_id = raw_id[:-4]
        clean_id = sanitize_echo_id(raw_id, fallback="")
        if not clean_id:
            return wrap_tool_output(
                text=f"❌ Identifiant `output_id` invalide : `{output_id}`.",
                status={"status": "error", "error_code": "invalid_output_id"},
            )

        out_dir = get_echo_session_path(uid, cid, "tools_output")
        file_path = os.path.join(out_dir, f"{clean_id}.txt")
        if not os.path.isfile(file_path):
            return wrap_tool_output(
                text=f"❌ Fichier `tools_output/{clean_id}.txt` introuvable (déjà supprimé ou identifiant erroné).",
                status={"status": "error", "error_code": "not_found", "output_id": clean_id},
            )

        def _read_lines():
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()

        lines = await asyncio.to_thread(_read_lines)
        total_lines = len(lines)

        if search_pattern and isinstance(search_pattern, str) and search_pattern.strip():
            pat_str = search_pattern.strip()
            try:
                regex = re.compile(pat_str, re.IGNORECASE)
            except re.error:
                regex = re.compile(re.escape(pat_str), re.IGNORECASE)

            matched_indices = [i for i, ln in enumerate(lines) if regex.search(ln)]
            if not matched_indices:
                return wrap_tool_output(
                    text=f"ℹ️ Aucune occurrence de `{pat_str}` trouvée dans `{clean_id}` ({total_lines} lignes).",
                    status={"status": "success", "output_id": clean_id, "matches": 0, "total_lines": total_lines},
                )

            blocks = []
            acc_bytes = 0
            shown_matches = 0
            last_end = -1
            for idx in matched_indices:
                ctx_s = max(0, idx - 2)
                ctx_e = min(total_lines - 1, idx + 2)
                if ctx_s <= last_end:
                    ctx_s = last_end + 1
                if ctx_s > ctx_e:
                    shown_matches += 1
                    continue
                chunk_lines = [f"L{ln_no + 1}: {lines[ln_no]}" for ln_no in range(ctx_s, ctx_e + 1)]
                block_str = "\n".join(chunk_lines)
                block_bytes = len(block_str.encode("utf-8"))
                if acc_bytes + block_bytes > ECHO_CODEX_MAX_READ_BYTES and blocks:
                    break
                if last_end != -1 and ctx_s > last_end + 1:
                    blocks.append("...")
                blocks.append(block_str)
                acc_bytes += block_bytes
                last_end = ctx_e
                shown_matches += 1

            body = "\n".join(blocks)
            return wrap_tool_output(
                text=(
                    f"🔍 **Recherche dans `{clean_id}`** (`{pat_str}` — {shown_matches}/{len(matched_indices)} occurrences affichées sur {total_lines} lignes) :\n\n"
                    f"```text\n{body}\n```"
                ),
                status={
                    "status": "success",
                    "output_id": clean_id,
                    "matches": len(matched_indices),
                    "shown_matches": shown_matches,
                    "total_lines": total_lines,
                },
            )

        page = paginate_lines_window(
            lines,
            start_line=start_line or 1,
            end_line=end_line,
            with_line_numbers=True,
        )
        s = page["start_line"]
        if page["out_of_range"]:
            return wrap_tool_output(
                text=f"⚠️ `start_line={s}` dépasse le nombre total de lignes de `{clean_id}` ({total_lines} lignes).",
                status={"status": "warning", "output_id": clean_id, "total_lines": total_lines},
            )
        selected = page["selected_lines"]
        actual_e = page["end_line"]
        has_more = page["has_more"]
        next_s = page["next_start_line"]
        next_e = page["next_end_line"]
        footer = (
            f"\n\nℹ️ Suite disponible : `read_tool_output(output_id=\"{clean_id}\", start_line={next_s}, end_line={next_e})`. "
            f"En fin d'exploitation : `delete_tool_output(output_id=\"{clean_id}\")`."
            if has_more
            else f"\n\n✅ Fin du fichier atteinte ({total_lines} lignes). Penser à appeler `delete_tool_output(output_id=\"{clean_id}\")` une fois l'exploitation terminée."
        )
        return wrap_tool_output(
            text=f"📄 **Sortie `{clean_id}` (Lignes {s} à {actual_e} sur {total_lines})** :\n\n```text\n" + "\n".join(selected) + f"\n```{footer}",
            status={
                "status": "success",
                "output_id": clean_id,
                "start_line": s,
                "end_line": actual_e,
                "total_lines": total_lines,
                "has_more": has_more,
                "next_start_line": next_s,
            },
        )

    @echo_tool
    async def delete_tool_output(
        self,
        output_id: str,
        __user__: dict = {},
        __metadata__: dict = {},
    ) -> dict:
        """Supprime un fichier de sortie d'outil délesté (`output_id`) dans `tools_output` (ou tous les fichiers de la session si `output_id='all'`)."""
        uid = (__user__ or {}).get("id")
        cid = (__metadata__ or {}).get("chat_id")
        if not uid or not cid:
            return wrap_tool_output(
                text="❌ Contexte utilisateur ou chat_id manquant.",
                status={"status": "error", "error_code": "missing_context"},
            )

        out_dir = get_echo_session_path(uid, cid, "tools_output")
        raw_id = (output_id or "").strip()
        if raw_id.lower() in ("*", "all"):
            removed = 0
            if os.path.isdir(out_dir):
                for fname in os.listdir(out_dir):
                    if fname.endswith(".txt"):
                        try:
                            os.remove(os.path.join(out_dir, fname))
                            removed += 1
                        except Exception:
                            pass
            return wrap_tool_output(
                text=f"🗑️ Purge de `tools_output` effectuée ({removed} fichier(s) supprimé(s)).",
                status={"status": "success", "deleted_count": removed},
            )

        if raw_id.endswith(".txt"):
            raw_id = raw_id[:-4]
        clean_id = sanitize_echo_id(raw_id, fallback="")
        file_path = os.path.join(out_dir, f"{clean_id}.txt") if clean_id else ""
        if not clean_id or not os.path.isfile(file_path):
            return wrap_tool_output(
                text=f"ℹ️ Fichier `tools_output/{clean_id or raw_id}.txt` déjà absent.",
                status={"status": "success", "output_id": clean_id, "deleted": False},
            )
        await asyncio.to_thread(os.remove, file_path)
        return wrap_tool_output(
            text=f"🗑️ Sortie temporaire `{clean_id}` supprimée de `tools_output`.",
            status={"status": "success", "output_id": clean_id, "deleted": True},
        )
