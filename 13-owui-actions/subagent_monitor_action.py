"""
title: ECHO Sub-Agent Monitor
author: Wilfried BARNAVON
version: 1.1
description: 1.0: HUD de visualisation arborescente des agents cognitifs ECHO.
             1.1: Boucle événementielle bidirectionnelle (pattern Codex).
             Suppression des troncatures Python. Refresh live via Promise.
             Icône arbre d'orchestration.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxjaXJjbGUgY3g9IjEyIiBjeT0iNCIgcj0iMiIvPjxsaW5lIHgxPSIxMiIgeTE9IjYiIHgyPSIxMiIgeTI9IjkiLz48bGluZSB4MT0iMTIiIHkxPSI5IiB4Mj0iNiIgeTI9IjEzIi8+PGxpbmUgeDE9IjEyIiB5MT0iOSIgeDI9IjE4IiB5Mj0iMTMiLz48Y2lyY2xlIGN4PSI2IiBjeT0iMTUiIHI9IjIiLz48Y2lyY2xlIGN4PSIxOCIgY3k9IjE1IiByPSIyIi8+PGxpbmUgeDE9IjYiIHkxPSIxNyIgeDI9IjYiIHkyPSIyMCIvPjxsaW5lIHgxPSIxOCIgeTE9IjE3IiB4Mj0iMTgiIHkyPSIyMCIvPjxjaXJjbGUgY3g9IjYiIGN5PSIyMSIgcj0iMSIvPjxjaXJjbGUgY3g9IjE4IiBjeT0iMjEiIHI9IjEiLz48L3N2Zz4=
"""

import sys
import re
import orjson as json
from pydantic import BaseModel, Field
from typing import Any, Optional

# Importations ECHO
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, EchoStateManager
from echo_ui import EchoUI


class Action:
    class Valves(BaseModel):
        priority: int = Field(default=3, description="Priorité d'affichage dans le menu Actions.")

    def __init__(self):
        self.valves = self.Valves()

    def _classify_thread(self, sub_sid: str, role_id: str) -> dict:
        """Classifie un thread par son préfixe et retourne type/icône/couleur."""
        if sub_sid.startswith("dlg_"):
            return {"type": "delegate", "icon": "🤖", "color": "#38bdf8", "label": "Delegate"}
        elif sub_sid.startswith("thread_"):
            return {"type": "expert", "icon": "🎓", "color": "#a78bfa", "label": role_id or "Expert"}
        else:
            return {"type": "generic", "icon": "🧠", "color": "#10b981", "label": role_id or "Thread"}

    def _parse_step(self, step: dict) -> list:
        """Transforme un step brut en nœuds d'arbre exploitables par le JS.
        Aucune troncature — le JS gère l'affichage expand/collapse."""
        nodes = []
        role = step.get("role", "user")
        parts = step.get("parts", [])
        ts = step.get("timestamp", 0)
        idx = step.get("index", 0)

        for part in parts:
            node = {"role": role, "index": idx, "timestamp": ts}

            if "functionCall" in part:
                fc = part["functionCall"]
                fn_name = fc.get("name", "?")
                fn_args = fc.get("args", {})

                # Détection d'escalade cognitive
                if fn_name == "new_cognitive_level":
                    target = fn_args.get("niveau_requis", "?")
                    node["type"] = "escalation"
                    node["content"] = f"→ {target}"
                else:
                    node["type"] = "functionCall"
                    node["fn_name"] = fn_name
                    # Args complets — pas de troncature
                    args_summary = {}
                    for k, v in fn_args.items():
                        args_summary[k] = str(v)
                    node["fn_args"] = args_summary

            elif "functionResponse" in part:
                fr = part["functionResponse"]
                fn_name = fr.get("name", "?")
                response = fr.get("response", {})
                if isinstance(response, dict):
                    status = response.get("status", "ok")
                    text = response.get("text", response.get("message", ""))
                    if not text:
                        text = str(response)
                else:
                    status = "ok"
                    text = str(response)
                node["type"] = "functionResponse"
                node["fn_name"] = fn_name
                node["status"] = status
                node["content"] = text

            elif "text" in part and not part.get("thought"):
                text = part["text"]
                if "QUESTION:" in text:
                    node["type"] = "question"
                    m = re.search(r"QUESTION:\s*(.+?)$", text, re.MULTILINE)
                    node["content"] = m.group(1).strip() if m else text
                    node["progress"] = text[:text.rfind("QUESTION:")].strip()
                else:
                    node["type"] = "text"
                    node["content"] = text
            else:
                continue

            nodes.append(node)

        return nodes

    def _build_threads_data(self, state: EchoStateManager, chat_id: str) -> list:
        """Reconstruit la structure arborescente depuis SQLite."""
        threads = state.list_threads(chat_id)
        threads_data = []
        for t in threads:
            sid = t["sub_sid"]
            classification = self._classify_thread(sid, t.get("role_id", ""))
            raw_steps = state.get_thread_steps_enriched(sid)

            tree_nodes = []
            for step in raw_steps:
                parsed = self._parse_step(step)
                tree_nodes.extend(parsed)

            threads_data.append({
                "sid": sid,
                "type": classification["type"],
                "icon": classification["icon"],
                "color": classification["color"],
                "label": classification["label"],
                "role_id": t.get("role_id", ""),
                "steps_count": t.get("last_step", 0) + 1,
                "updated_at": t.get("updated_at", 0),
                "nodes": tree_nodes,
            })
        return threads_data

    async def action(self, body: dict, __user__: dict = {},
                     __metadata__: dict = {},
                     __event_emitter__=None, __event_call__=None, **kwargs):
        events = EchoEvents(__event_emitter__, __event_call__)

        if not __event_call__:
            return None

        uid = __user__.get("id")
        chat_id = body.get("chat_id") or __metadata__.get("chat_id")

        if not uid:
            await events.toast("❌ Utilisateur non identifié.", "error")
            return None
        if not chat_id:
            await events.toast("❌ Aucun chat_id détecté.", "error")
            return None

        await events.status("🧠 Chargement des agents cognitifs...", False)

        state = EchoStateManager(user_id=uid, chat_id=chat_id)
        threads_data = self._build_threads_data(state, chat_id)

        if not threads_data:
            await events.status("🧠 Aucun agent cognitif actif.", True)
            await events.toast("ℹ️ Aucun thread cognitif trouvé pour ce chat.", "info")
            return None

        # 1. Injection initiale du HUD
        threads_json = json.dumps(threads_data).decode("utf-8")
        hud_js = EchoUI._generate_subagent_monitor_js(threads_json, chat_id)
        await __event_call__({"type": "execute", "data": {"code": hud_js}})
        await events.status("🧠 Cognitive Monitor actif.", True)

        # 2. Boucle événementielle bidirectionnelle (pattern Codex)
        while True:
            wait_code = "return new Promise(r => window.echoSubagentResolve = r);"
            response = await __event_call__({"type": "execute", "data": {"code": wait_code}})

            if not response or not isinstance(response, dict):
                break

            action_type = response.get("action")

            if action_type == "close":
                break

            elif action_type == "refresh":
                # Relecture SQLite et push des nouvelles données
                threads_data = self._build_threads_data(state, chat_id)
                threads_json = json.dumps(threads_data).decode("utf-8")
                update_js = f"if(window.echoMonitorUpdate) window.echoMonitorUpdate({threads_json});"
                await __event_call__({"type": "execute", "data": {"code": update_js}})

            else:
                break

        return {"status": "success"}
