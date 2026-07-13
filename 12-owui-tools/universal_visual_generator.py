"""
title: ECHO Visual Engine
author: Wilfried BARNAVON
version: 5.9
description: Composant système interne : ECHO Visual Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.9: Implémentation de la boucle de rétroaction sémantique (Pydantic) via call_cascade (suppression call_distillation).
# 5.8: Intégration du moteur cartographique Leaflet (v1.9) et ajout du manuel des moteurs dans la docstring.
# 5.6: Fix - Intégration de TEMP_DEFAULT et TOP_P_DEFAULT dans le payload de génération via call_cascade.
# 5.5: Optimisation du prompt Architecte Visuel (balises XML, bloc <thinking>, ton impersonnel).
# 5.1: Ajout de la contrainte de précision syntaxique Mermaid v11 (identifiants de nœuds sans caractères spéciaux). 5.2: Renommage niveau_cognitif→target_model, migration stream→call_cascade() centralisé.
# MOTEURS INTÉGRÉS ET DATAFLOWS :
# - mermaid (v11) : Syntaxe Mermaid pure
# - echarts (v5) : JSON ECharts strict
# - markmap (v0.17) : Markdown hiérarchique
# - leaflet (v1.9) : JSON {"center": [lat,lng], "zoom": int, "markers": [{"lat": float, "lng": float, "popup": "html"}]}
# - vega (v5), cytoscape, wavedrom, timeline, aframe, etc.

import os
import sys
import re
import base64
import orjson as json
from typing import Optional, Any, Tuple, Union, Literal, List
from pydantic import BaseModel, Field, ValidationError

class LeafletMarker(BaseModel):
    lat: float
    lng: float
    popup: Optional[str] = None

class LeafletSchema(BaseModel):
    center: Tuple[float, float]
    zoom: int
    markers: List[LeafletMarker] = []
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, wrap_cascade_output, EchoGeminiClient, clamp_model
from echo_ui import EchoUI
from echo_constants import (
    MODEL_FLASH, MODEL_ROUTING,
    TEMP_DEFAULT, TOP_P_DEFAULT
)



class Tools:
  class Valves(BaseModel):
    GENERATOR_TIMEOUT: int = Field(default=60, description="Délai d'attente maximum (secondes) pour la génération Rendu Visuel.")
    CDN_TIMEOUT_MS: int = Field(default=10000, description="Délai maximum (en ms) d'attente pour le chargement des librairies graphiques via CDN dans le navigateur.")

  def __init__(self):
    self.valves = self.Valves()

  async def generate_rich_visualization(
    self,
    intention: str,
    donnees_contextuelles: str,
    # [MAINTENANCE_AI] Avertissement: Toujours mettre à jour ce Literal lors de l'ajout/suppression d'un moteur de rendu.
    moteur: Optional[Literal["markmap", "mermaid", "sketch", "echarts", "vega", "timeline", "bpmn", "gantt", "aframe", "svg", "cytoscape", "wavedrom", "chem", "science", "bio", "astro", "leaflet"]] = None,
    niveau_cognitif: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_FLASH",
    __user__: dict = {},
    __metadata__: dict = {},
    __event_emitter__: Any = None,
    __event_call__: Any = None
  ) -> Union[dict, Tuple[HTMLResponse, dict]]:
    """
    Génération asynchrone d'interfaces interactives (Mindmaps, Graphes, Tableaux). Sub-chat MODEL_FLASH. Retourne le composant ECHO Visual.
    :param intention: Objectif du rendu visuel.
    :param donnees_contextuelles: Données brutes à modéliser.
    :param moteur: (Optionnel) markmap, mermaid, echarts, etc.
    :param niveau_cognitif: (Optionnel) Enum des modèles (echo_constants).
    """
    events = EchoEvents(__event_emitter__, __event_call__)
    await events.status(f"🧠 Rendu Visuel : Orchestration {moteur or 'Auto'}...")
    user_id = __user__.get("id", "system") if __user__ else "system"
    target_model = niveau_cognitif

    # 1. Manuel Technique de l'Architecte
    directive_moteur = f"Le Modèle DOIT impérativement utiliser le moteur : '{moteur}'." if moteur else "Le Modèle DOIT choisir le moteur le plus adapté."
    
    system_prompt = (
        "<persona>\n"
        "Le Modèle est un architecte technique expert en génération de représentations visuelles.\n"
        "</persona>\n\n"
        "<mission>\n"
        "Le Modèle doit transformer une intention textuelle et un jeu de données en un payload technique certifié et fonctionnel.\n"
        "</mission>\n\n"
        f"<directive>\n"
        f"{directive_moteur}\n"
        f"</directive>\n\n"
        "<technical_manual>\n"
        "1. 'markmap' : Markdown hiérarchique pur. Aucun bloc de code.\n"
        "2. 'mermaid' : Syntaxe stricte compatible Mermaid v11.16.0. Identifiants de nœuds STRICTEMENT ASCII alphanumériques ou underscore (aucun espace/tiret). Texte lisible encapsulé entre guillemets (ex: ID[\"Texte\"]).\n"
        "3. 'echarts' : JSON ECharts 5+ valide (inclure tooltip, legend, xAxis, yAxis, series). Thème clair.\n"
        "4. 'vega' : JSON Vega-Lite strict (spécifier $schema, data, mark, encoding).\n"
        "5. 'timeline' : JSON TimelineJS. Structure imposée: {\"events\": [{\"start_date\":..., \"text\":{\"headline\":..., \"text\":...}}]}.\n"
        "6. 'bpmn' : XML BPMN 2.0 valide.\n"
        "7. 'gantt' : Syntaxe Mermaid Gantt pure (débute par 'gantt').\n"
        "8. 'aframe' : HTML A-Frame (<a-scene>, <a-box>, etc.).\n"
        "9. 'cytoscape' : JSON Cytoscape.js (elements: {\"nodes\": [], \"edges\": []}).\n"
        "10. 'wavedrom' : JSON WaveDrom (signal: []).\n"
        "11. 'astro' : JSON Celestial (projection: 'orthographic', transform: 'equatorial').\n"
        "12. 'bio' : Renvoie UNIQUEMENT l'ID PDB (ex: 1A8M) ou le contenu complet d'un fichier PDB.\n"
        "13. 'svg' : XML SVG complet et valide.\n"
        "14. 'chem' : Chaîne SMILES (ex: 'CC(=O)OC1=CC=CC=C1C(=O)O').\n"
        "15. 'science' : JSON Plotly.js (data: [], layout: {}).\n"
        "16. 'leaflet' : JSON strict pour carte géographique. Structure imposée: {\"center\": [lat, lng], \"zoom\": int, \"markers\": [{\"lat\": float, \"lng\": float, \"popup\": \"texte html\"}]}.\n"
        "</technical_manual>\n\n"
        "<rules>\n"
        "1. RÉFLEXION : Le Modèle DOIT structurer sa réflexion analytique préalable dans une balise <thinking>.\n"
        "2. EXÉCUTION : Le Modèle DOIT renvoyer UNIQUEMENT le payload technique encapsulé dans un bloc de code (```).\n"
        "3. SILENCE : Le Modèle a l'INTERDICTION absolue d'ajouter du texte ou des commentaires en dehors de la balise <thinking> et du bloc de code.\n"
        "</rules>\n\n"
        "<example>\n"
        "<thinking>\n"
        "Processus séquentiel requis. Choix du moteur: Mermaid (sequenceDiagram). Vérification: Les identifiants de participants doivent être strictement alphanumériques (User1, SystemA).\n"
        "</thinking>\n"
        "```mermaid\n"
        "sequenceDiagram\n"
        f"    participant User1\n"
        f"    participant SystemA\n"
        f"    User1->>SystemA: Request\n"
        f"```\n"
        f"</example>"
    )

    # 3. Génération
    try:
      # Génération via call_cascade (clamping + thinking auto + cascade)
      data, model_used, _ = await EchoGeminiClient.call_cascade(
          target_model_key=niveau_cognitif,
          payload={
              "contents": [{"role": "user", "parts": [{"text": f"INTENTION : {intention}\nDONNÉES : {donnees_contextuelles}"}]}],
              "systemInstruction": {"parts": [{"text": system_prompt}]},
              "generationConfig": {"temperature": TEMP_DEFAULT, "topP": TOP_P_DEFAULT}
          },
          user_id=user_id,
          metadata=__metadata__,
          events=events,
          timeout=self.valves.GENERATOR_TIMEOUT,
          include_thoughts=False,
      )
      if not data:
          return wrap_tool_output(text="❌ Cascade épuisée : aucun modèle disponible pour la génération visuelle.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
      # Extraction texte depuis la réponse
      response_text = ""
      candidates = data.get("candidates", [])
      if candidates:
          for p in candidates[0].get("content", {}).get("parts", []):
              if "text" in p: response_text += p["text"]

      # Extraction du payload (Pattern Bloc de Code ou Texte Brut)
      payload = ""
      code_match = re.search(r"```(?:\w+)?\n?(.*?)\n?```", response_text, re.DOTALL)
      payload = code_match.group(1).strip() if code_match else response_text.strip()

      if not payload:
        return wrap_tool_output(text="⚠️ Échec : Le modèle n'a pas généré de payload valide.", status={"status": "empty_response"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

      # Détermination du moteur final
      final_moteur = moteur
      if not final_moteur:
        for m in ["bio", "svg", "chem", "science", "astro", "aframe", "cytoscape", "wavedrom", "timeline", "bpmn", "echarts", "vega", "markmap", "gantt", "sketch", "leaflet", "mermaid"]:
          if m in response_text.lower()[:300]:
            final_moteur = m
            break
      final_moteur = final_moteur or "mermaid"

      # --- VALIDATION JSON STRICTE (Moteurs Data) ---
      moteurs_json = ["echarts", "vega", "timeline", "cytoscape", "wavedrom", "science", "astro", "leaflet"]
      if final_moteur in moteurs_json:
          try:
              data = json.loads(payload)
              if final_moteur == "leaflet":
                  LeafletSchema(**data)
          except Exception as e:
              await events.status(f"⚠️ Rétroaction Syntaxique/Structurelle ({final_moteur})...")
              repair_prompt = (
                  "<feedback>\n"
                  f"Le payload généré pour le moteur '{final_moteur}' a échoué à la validation.\n"
                  "</feedback>\n\n"
                  "<error>\n"
                  f"{str(e)}\n"
                  "</error>\n\n"
                  "<instruction>\n"
                  "Le Modèle DOIT analyser l'erreur ci-dessus et corriger immédiatement ce JSON pour respecter strictement le schéma imposé.\n"
                  "Le Modèle DOIT renvoyer UNIQUEMENT le bloc de code corrigé.\n"
                  "</instruction>\n\n"
                  "<invalid_payload>\n"
                  f"{payload}\n"
                  "</invalid_payload>"
              )
              payload_fixed_data, _, _ = await EchoGeminiClient.call_cascade(
                  target_model_key=niveau_cognitif,
                  payload={
                      "contents": [{"role": "user", "parts": [{"text": repair_prompt}]}]
                  },
                  user_id=user_id,
                  metadata=__metadata__,
                  events=events,
                  timeout=30,
                  include_thoughts=False
              )
              if payload_fixed_data:
                  candidates = payload_fixed_data.get("candidates", [])
                  if candidates:
                      fixed_text = "".join([p["text"] for p in candidates[0].get("content", {}).get("parts", []) if "text" in p])
                      code_match = re.search(r"```(?:\w+)?\n?(.*?)\n?```", fixed_text, re.DOTALL)
                      payload = code_match.group(1).strip() if code_match else fixed_text.strip()

      await events.status("Génération terminée. Déploiement de l'interface...", done=True)

      # 4. Rendu
      response, context = EchoUI.generate_rich_view(
        moteur=final_moteur,
        payload=base64.b64encode(payload.encode()).decode(),
        title=f"ECHO Visual : {intention[:30]}...",
        cdn_timeout_ms=self.valves.CDN_TIMEOUT_MS
      )
      
      context.update({"moteur": final_moteur, "intention": intention})
      return response, wrap_tool_output(text=context["message"], status=context, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    except Exception as e:
      return wrap_tool_output(text=f"❌ Erreur lors de la génération visuelle : {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
