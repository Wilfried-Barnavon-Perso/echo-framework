"""
title: ECHO Agent Monitor
author: Wilfried BARNAVON
version: 1.7
description: HUD interactif de visualisation arborescente des sous-agents et experts cognitifs ECHO.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxjaXJjbGUgY3g9IjEyIiBjeT0iNCIgcj0iMiIvPjxsaW5lIHgxPSIxMiIgeTE9IjYiIHgyPSIxMiIgeTI9IjkiLz48bGluZSB4MT0iMTIiIHkxPSI5IiB4Mj0iNiIgeTI9IjEzIi8+PGxpbmUgeDE9IjEyIiB5MT0iOSIgeDI9IjE4IiB5Mj0iMTgiLz48Y2lyY2xlIGN4PSI2IiBjeT0iMTUiIHI9IjIiLz48Y2lyY2xlIGN4PSIxOCIgY3k9IjE1IiByPSIyIi8+PGxpbmUgeDE9IjYiIHkxPSIxNyIgeDI9IjYiIHkyPSIyMCIvPjxsaW5lIHgxPSIxOCIgeTE9IjE3IiB4Mj0iMTgiIHkyPSIyMCIvPjxjaXJjbGUgY3g9IjYiIGN5PSIyMSIgcj0iMSIvPjxjaXJjbGUgY3g9IjE4IiBjeT0iMjEiIHI9IjEiLz48L3N2Zz4=
"""
# Historique des versions :
# 1.6: Mise à jour de la priorité d'affichage à 50.
# 1.0: HUD de visualisation arborescente des agents cognitifs ECHO.
# 1.1: Boucle événementielle bidirectionnelle (pattern Codex).
# Suppression des troncatures Python. Refresh live via Promise.
# Icône arbre d'orchestration.
# 1.2: Renommage subagent→agent. Classification enrichie par préfixe
# (agent, expert, council, supervisor). Regroupement des threads de
# conseil et de supervision sous onglets uniques.
# 1.3: Stratégie multi-agentique : fusion chronologique des conseils (chat)
# et regroupement arborescent des workers sous le superviseur.
# 1.4: Optimisation du rendu HUD multi-agent et intégration du nouveau moteur EchoUI.
# 1.5: Classification spécifique Navigateur Web et filtrage HUD de la carte DOM.

import sys
import re
import orjson as json
from pydantic import BaseModel, Field

# Importations ECHO
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, EchoStateManager
from echo_ui import EchoUI


class Action:
    class Valves(BaseModel):
        priority: int = Field(default=50, description="Priorité d'affichage dans le menu Actions.")

    def __init__(self):
        self.valves = self.Valves()

    def _classify_thread(self, sub_sid: str, role_id: str) -> dict:
        """Classifie un thread par son préfixe et retourne type/icône/couleur."""
        if sub_sid.startswith("thread_web_"):
            return {"type": "navigator", "icon": "🌐", "color": "#0ea5e9", "label": "Navigateur Web"}
        elif sub_sid.startswith("thread_council_"):
            return {"type": "council", "icon": "🏛️", "color": "#f59e0b", "label": "Conseil"}
        elif sub_sid.startswith("thread_supervisor_"):
            return {"type": "supervisor", "icon": "📋", "color": "#8b5cf6", "label": "Superviseur"}
        elif sub_sid.startswith("thread_"):
            # Expert qualifié — extraire le role_name du préfixe thread_{role}_{uuid}
            parts = sub_sid.split("_", 2)
            expert_label = parts[1] if len(parts) >= 2 else role_id or "Expert"
            return {"type": "expert", "icon": "🎓", "color": "#a78bfa", "label": f"Expert ({expert_label})"}
        elif sub_sid.startswith("dlg_"):
            return {"type": "agent", "icon": "🤖", "color": "#38bdf8", "label": "Agent"}
        else:
            return {"type": "generic", "icon": "🧠", "color": "#10b981", "label": role_id or "Thread"}

    def _parse_step(self, step: dict, expert_alias: str = "") -> list:
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
                    if str(status).lower() in ["error", "failed", "false"]:
                        status = "error"
                    else:
                        status = "ok"
                    text = response.get("text", response.get("message", ""))
                    if not text:
                        if "dom_map" in response:
                            clean_resp = {k: v for k, v in response.items() if k != "dom_map"}
                            clean_resp["dom_map"] = f"[Carte DOM : {len(response['dom_map'])} éléments]"
                            text = str(clean_resp)
                        else:
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
                
                if expert_alias and role == "model":
                    node["expert_alias"] = expert_alias

            else:
                continue

            nodes.append(node)

        return nodes

    def _build_threads_data(self, state: EchoStateManager, chat_id: str) -> list:
        """Reconstruit la structure arborescente et multi-agent depuis SQLite."""
        threads = state.list_threads(chat_id)
        
        # 1. Parsing initial
        parsed_threads = []
        for t in threads:
            sid = t["sub_sid"]
            role_id = t.get("role_id", "")
            classification = self._classify_thread(sid, role_id)
            
            # Extraction de l'alias si expert
            expert_alias = ""
            if sid.startswith("thread_council_"):
                parts = sid.split("_", 3)
                if len(parts) >= 4:
                    expert_alias = parts[3]
            elif classification["type"] == "expert":
                expert_alias = classification["label"]

            raw_steps = state.get_thread_steps_enriched(sid)
            tree_nodes = []
            for step in raw_steps:
                parsed = self._parse_step(step, expert_alias)
                tree_nodes.extend(parsed)

            parsed_threads.append({
                "sid": sid,
                "type": classification["type"],
                "icon": classification["icon"],
                "color": classification["color"],
                "label": classification["label"],
                "role_id": role_id,
                "steps_count": t.get("last_step", 0) + 1,
                "updated_at": t.get("updated_at", 0),
                "nodes": tree_nodes,
            })

        # 2. Groupement et fusion multi-agent
        final_threads = []
        councils = {}
        supervisors = {}
        
        for pt in parsed_threads:
            sid = pt["sid"]
            if sid.startswith("thread_council_"):
                parts = sid.split("_", 3)
                if len(parts) >= 3:
                    cid = parts[2]
                    councils.setdefault(cid, []).append(pt)
                else:
                    final_threads.append(pt)
            elif sid.startswith("thread_supervisor_"):
                parts = sid.split("_", 3)
                if len(parts) >= 3:
                    tid = parts[2]
                    supervisors.setdefault(tid, []).append(pt)
                else:
                    final_threads.append(pt)
            else:
                final_threads.append(pt)

        # 3a. Assemblage des conseils (Fusion chronologique)
        for cid, members in councils.items():
            all_nodes = []
            max_updated = 0
            total_steps = 0
            for m in members:
                all_nodes.extend(m["nodes"])
                max_updated = max(max_updated, m["updated_at"])
                total_steps += m["steps_count"]
            
            # Tri par timestamp chronologique
            all_nodes.sort(key=lambda n: (n.get("timestamp", 0), n.get("index", 0)))
            
            final_threads.append({
                "sid": f"ccl_{cid}",
                "type": "council",
                "icon": "🏛️",
                "color": "#f59e0b",
                "label": f"Conseil {cid}",
                "role_id": "council",
                "steps_count": total_steps,
                "updated_at": max_updated,
                "nodes": all_nodes,
            })

        # 3b. Assemblage des superviseurs (Arborescence)
        for tid, workers in supervisors.items():
            all_nodes = []
            max_updated = 0
            total_steps = 0
            for w in workers:
                worker_id = w["sid"].split("_", 3)[3] if len(w["sid"].split("_", 3)) >= 4 else "Inconnu"
                max_updated = max(max_updated, w["updated_at"])
                total_steps += w["steps_count"]
                
                # Ajout de la racine du worker
                first_ts = w["nodes"][0].get("timestamp", 0) if w["nodes"] else 0
                all_nodes.append({
                    "type": "worker_branch",
                    "role": "system",
                    "content": f"Worker: {worker_id}",
                    "indent_override": 0,
                    "timestamp": first_ts
                })
                
                # Injection de l'indentation pour les étapes réelles
                for n in w["nodes"]:
                    n["indent_override"] = 1
                    all_nodes.append(n)
                    
            final_threads.append({
                "sid": f"sup_{tid}",
                "type": "supervisor",
                "icon": "📋",
                "color": "#8b5cf6",
                "label": f"Supervision {tid}",
                "role_id": "supervisor",
                "steps_count": total_steps,
                "updated_at": max_updated,
                "nodes": all_nodes,
            })

        # Tri final par updated_at décroissant
        final_threads.sort(key=lambda t: t["updated_at"], reverse=True)
        return final_threads

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
        hud_js = EchoUI._generate_agent_monitor_js(threads_json, chat_id)
        await __event_call__({"type": "execute", "data": {"code": hud_js}})
        await events.status("🧠 Cognitive Monitor actif.", True)

        # 2. Boucle événementielle bidirectionnelle (pattern Codex)
        while True:
            wait_code = "return new Promise(r => window.echoAgentResolve = r);"
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
