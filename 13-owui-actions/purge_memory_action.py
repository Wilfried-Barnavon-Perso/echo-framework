"""
title: ECHO Memory Purge (Granulaire)
author: Wilfried BARNAVON
version: 2.7
description: 2.7: Tri multi-critères des tags (Importance, Fréquence, Alpha) et support des plages.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwb2x5Z29uIHBvaW50cz0iMjIgMyAyIDMgMTAgMTIuNDYgMTAgMTkgMTQgMjEgMTQgMTIuNDYgMjIgMyIvPjwvc3ZnPg==
"""

import sys
import httpx
import orjson as json
from pydantic import BaseModel, Field
from typing import Any, Optional, List, Set, Dict

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents
from echo_constants import COLLECTION_MEMORY

class Action:
    class Valves(BaseModel):
        QDRANT_URL: str = Field(default="http://echo-qdrant:6333", description="URL interne de Qdrant.")
        priority: int = Field(default=2, description="Priorité d'affichage.")

    def __init__(self):
        self.valves = self.Valves()

    async def _get_all_user_tags_enriched(self, user_id: str) -> List[Dict]:
        """Récupère et agrège les tags avec métadonnées (importance, fréquence, nom)."""
        tag_data: Dict[str, Dict] = {} # {tag_name: {count: X, max_imp: Y}}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                scroll_payload = {
                    "filter": {"must": [{"key": "user_id", "match": {"value": user_id}}]},
                    "limit": 200, # Large scan pour agrégation
                    "with_payload": ["tags", "memory_importance"]
                }
                
                resp = await client.post(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/scroll",
                    json=scroll_payload
                )
                
                if resp.status_code == 200:
                    results = resp.json().get("result", {}).get("points", [])
                    for p in results:
                        payload = p.get("payload", {})
                        p_tags = payload.get("tags", [])
                        imp = int(payload.get("memory_importance", payload.get("importance", 1))) # fallback compatibilité
                        
                        if isinstance(p_tags, list):
                            for t in p_tags:
                                if t not in tag_data:
                                    tag_data[t] = {"name": t, "count": 0, "max_imp": 0}
                                tag_data[t]["count"] += 1
                                # Gestion de l'importance des souvenirs : conservation du score maximal
                                if imp > tag_data[t]["max_imp"]:
                                    tag_data[t]["max_imp"] = imp
                
                final_list = list(tag_data.values())
                
                # TRI HIERARCHIQUE :
                # 1. Importance/Criticité (ASC: la moindre en premier)
                # 2. Fréquence (DESC: plus de liaisons en premier)
                # 3. Alphabet (ASC)
                final_list.sort(key=lambda x: (x["max_imp"], -x["count"], x["name"]))
                
                return final_list
        except Exception as e:
            print(f"[ECHO-PURGE] Erreur tags enriched: {e}")
            return []

    async def _get_impacted_slugs(self, must_filters: List[Dict]) -> List[str]:
        """Effectue un Dry Run pour récupérer les slugs des souvenirs qui vont être supprimés."""
        slugs: Set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                search_payload = {
                    "filter": {"must": must_filters},
                    "limit": 50, # On limite l'affichage
                    "with_payload": ["slug"]
                }
                
                resp = await client.post(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/scroll",
                    json=search_payload
                )
                
                if resp.status_code == 200:
                    results = resp.json().get("result", {}).get("points", [])
                    for p in results:
                        slug = p.get("payload", {}).get("slug")
                        if slug: slugs.add(slug)
                return list(slugs)
        except: return []

    async def action(self, body: dict, __user__: Optional[dict] = None, __event_emitter__: Any = None, __event_call__: Any = None, **kwargs):
        events = EchoEvents(__event_emitter__, __event_call__)
        
        if not __user__ or "id" not in __user__:
            await events.toast("❌ Erreur : Utilisateur non identifié.", "error")
            return None

        user_id = __user__["id"]
        chat_id = body.get("chat_id")

        await events.status("🧠 Analyse multidimensionnelle de la mémoire...", False)
        
        # 1. Récupération enrichie
        enriched_tags = await self._get_all_user_tags_enriched(user_id)
        
        if not enriched_tags:
            await events.status("🧠 Aucun souvenir trouvé dans la mémoire organique.", True)
            await events.toast("Vous n'avez aucun souvenir enregistré.", "info")
            return None

        await events.status("🧠 Prêt pour la purge.", True)

        # Construction de la liste visuelle avec labels sémantiques
        imp_labels = {1: "🟢", 2: "🔵", 3: "🟡", 4: "🟠", 5: "🔴"}
        rows = []
        for i, t in enumerate(enriched_tags):
            label = imp_labels.get(t["max_imp"], "⚪")
            rows.append(f"<b>{i+1}.</b> {label} {t['name']} <i>({t['count']} souvenirs)</i>")
        
        tags_display = "<br>".join(rows)
        available_tag_names = [t["name"] for t in enriched_tags]

        # 2. Demande de sélection des tags par numéros (Support des plages type 1-5)
        selection_raw = await events.call("input", {
            "title": "🏷️ Purge sélective (Importance croissante)",
            "message": f"Catégories triées par importance :<br>{tags_display}<br><br>Saisissez les numéros (ex: 1, 3-5, 8) :",
            "type": "text",
            "placeholder": "Ex: 1-4, 7, 10-12"
        })

        if not selection_raw:
            return None

        # Conversion intelligente des numéros (Gestion des plages)
        selected_tags = []
        try:
            parts = [p.strip() for p in selection_raw.split(",")]
            indices = set()
            for p in parts:
                if "-" in p:
                    start, end = map(int, p.split("-"))
                    for i in range(start, end + 1): indices.add(i - 1)
                elif p.isdigit():
                    indices.add(int(p) - 1)
            
            for idx in sorted(list(indices)):
                if 0 <= idx < len(available_tag_names):
                    selected_tags.append(available_tag_names[idx])
        except Exception as e:
            print(f"[ECHO-PURGE] Erreur parsing selection: {e}")
        
        if not selected_tags:
            await events.toast("❌ Sélection invalide ou vide.", "error")
            return None

        # 3. Demande du périmètre (Scope) sécurisée
        scope_raw = await events.call("input", {
            "title": "🎯 Périmètre de la purge",
            "message": f"Vous avez sélectionné les catégories : {', '.join(selected_tags)}<br><br><b>Choisissez l'étendue de l'oubli :</b><br>1. Dans cette conversation uniquement<br>2. Dans TOUTE ma mémoire globale<br><br>Tapez 1 ou 2 :",
            "type": "text",
            "placeholder": "1 ou 2"
        })

        if not scope_raw:
            return None
            
        scope_choice = (scope_raw.strip() == "1")
        scope_text = "cette conversation" if scope_choice else "toute votre mémoire globale"
        
        # 4. DRY RUN (Prévisualisation des cibles exactes)
        must_filters = [
            {"key": "user_id", "match": {"value": user_id}},
            {"key": "tags", "match": {"any": selected_tags}}
        ]
        if scope_choice and chat_id:
            must_filters.append({"key": "chat_id", "match": {"value": chat_id}})
            
        impacted_slugs = await self._get_impacted_slugs(must_filters)
        
        if not impacted_slugs:
            await events.status("🧠 Aucun souvenir correspondant à ces critères.", True)
            await events.toast("Aucun souvenir trouvé pour ces tags dans ce périmètre.", "info")
            return None
            
        slug_preview = "<br>".join([f"• <i>{s}</i>" for s in impacted_slugs[:10]])
        if len(impacted_slugs) > 10:
            slug_preview += f"<br>... et {len(impacted_slugs) - 10} autres."

        # 5. Confirmation finale Sécurisée
        final_conf = await events.confirm(
            "⚠️ Confirmation finale de suppression",
            f"ECHO va oublier <b>{len(impacted_slugs)} souvenir(s)</b> associé(s) aux tags [{', '.join(selected_tags)}] dans {scope_text}.<br><br><b>Sujets impactés :</b><br>{slug_preview}<br><br>Cette action est irréversible. Continuer ?"
        )

        if not final_conf:
            return None

        await events.status(f"🧹 Purge en cours...", False)

        try:
            # 6. Suppression réelle
            delete_payload = {"filter": {"must": must_filters}}

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete",
                    json=delete_payload
                )

                if resp.status_code == 200:
                    await events.status("🧠 Mémoire purgée avec succès.", True)
                    await events.toast(f"✅ {len(impacted_slugs)} souvenirs oubliés.", "success")
                else:
                    await events.status("❌ Échec de la purge.", True)
                    await events.toast(f"❌ Erreur Qdrant : {resp.text}", "error")

        except Exception as e:
            await events.status("❌ Erreur système.", True)
            await events.toast(f"❌ Erreur : {str(e)}", "error")

        return None

