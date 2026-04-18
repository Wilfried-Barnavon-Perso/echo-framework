"""
title: ECHO Universal Visual Generator
author: Wilfried BARNAVON
version: 2.4
description: 2.4: Omniscience Visuelle - Orchestration multi-moteurs intégrale (13 moteurs). Suppression des références Rendu Visuel.
"""

import os
import sys
import re
import orjson as json
from typing import Optional, Any, Tuple, Union
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_ui import EchoRichUI
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL, MODEL_FLASH, MODEL_PRO, MODEL_LITE, MODEL_ROUTING

class Tools:
  class Valves(BaseModel):
    KEY_SWITCH_THRESHOLD: int = Field(default=3, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
    GENERATOR_TIMEOUT: int = Field(default=60, description="Délai d'attente maximum (secondes) pour la génération Rendu Visuel.")

  def __init__(self):
    self.valves = self.Valves()
    self.auth = EchoAuth()

  async def generate_rich_visualization(
    self,
    intention: str,
    donnees_contextuelles: str,
    niveau_cognitif: str = "MODEL_FLASH",
    __user__: dict = {},
    __event_emitter__: Any = None,
    __event_call__: Any = None
  ) -> Union[dict, Tuple[HTMLResponse, dict]]:
    """
    Déploie une interface visuelle interactive adaptée à votre besoin (OS de Rendu Visuel ECHO).
    DOMAINES : Mindmaps, Processus (IT/Métier), Stats, Finance, Géo, 3D, Chimie, Électronique, Gestion.
    
    :param intention: Description du besoin visuel (ex: "Plan 3D d'un bureau", "Structure de la molécule d'aspirine").
    :param donnees_contextuelles: Faits, chiffres et relations à modéliser.
    :param niveau_cognitif: 'MODEL_LITE', 'MODEL_FLASH' ou 'MODEL_PRO'.
    """
    events = EchoEvents(__event_emitter__, __event_call__)
    await events.status(f"🧠 Rendu Visuel : Orchestration multi-moteurs...")

    # 1. Auth
    api_keys = self.auth.get_api_keys(__user__.get("id"))
    if not api_keys: 
      return wrap_tool_output(text="❌ Configuration ECHO Requise : Aucune clé API trouvée.", status={"status": "error"})

    # 2. Boussole de Sélection (System Prompt du Sous-Agent)
    system_prompt = (
      "Tu es l'Architecte Visuel Souverain du Framework ECHO.\n"
      "Ta mission : transformer une intention et des données en un payload technique pour le moteur de rendu optimal.\n\n"
      "MATRICE DES MOTEURS DISPONIBLES :\n"
      "1. 'markmap' : Mindmaps et hiérarchies simples. (Markdown).\n"
      "2. 'mermaid' : Séquences, Flowcharts techniques, UML. (Mermaid.js).\n"
      "3. 'echarts' : Tableaux de bord, finance, heatmaps pro. (JSON ECharts).\n"
      "4. 'vega' : Science des données, statistiques complexes. (JSON Vega-Lite).\n"
      "5. 'timeline' : Histoire, événements chronologiques. (JSON TimelineJS).\n"
      "6. 'bpmn' : Processus métiers normés (ITIL, Droit, RH). (XML BPMN).\n"
      "7. 'gantt' : Plannings de projet et jalons. (JSON Gantt).\n"
      "8. 'aframe' : Scènes 3D, architecture, volumes. (HTML A-Frame).\n"
      "9. 'svg' : Plans 2D, schémas sur mesure avec zoom. (Code SVG).\n"
      "10. 'leaflet' : Cartographie de données géographiques. (JSON/GeoJSON).\n"
      "11. 'cytoscape' : Réseaux, graphes de force, clusters. (JSON Elements).\n"
      "12. 'smiles' : Structures moléculaires et chimie. (Syntaxe SMILES brute).\n"
      "13. 'wavedrom' : Chronogrammes et signaux numériques. (JSON WaveDrom).\n\n"
      "RÈGLE ABSOLUE :\n"
      "Réponds EXCLUSIVEMENT par un objet JSON valide :\n"
      "{\n"
      " \"moteur\": \"nom_du_moteur\",\n"
      " \"payload\": \"...le code brut ou JSON...\",\n"
      " \"explication\": \"Justification du choix pour ECHO\"\n"
      "}\n"
    )

    payload_request = {
      "systemInstruction": {"parts": [{"text": system_prompt}]},
      "contents": [
        {
          "role": "user",
          "parts": [
            {"text": f"INTENTION : {intention}\n\nDONNÉES : {donnees_contextuelles}"}
          ]
        }
      ]
    }
    
    # 3. Délégation Cognitive (Sans cascade automatique)
    target_model = MODEL_ROUTING.get(niveau_cognitif, niveau_cognitif)
    try:
      data = await EchoGeminiClient.call(
        keys=api_keys, target_model=target_model, payload=payload_request,
        threshold=self.valves.KEY_SWITCH_THRESHOLD, events=events, timeout=self.valves.GENERATOR_TIMEOUT
      )
      
      cand = data.get("candidates", [])[0]
      raw_response = ""
      if "content" in cand:
        for p in cand["content"].get("parts", []):
          if "text" in p: raw_response += p["text"]
      
      # Nettoyage JSON du sous-agent
      raw_response = raw_response.strip()
      if "```json" in raw_response:
        raw_response = raw_response.split("```json")[1].split("```")[0].strip()
      elif "```" in raw_response:
        raw_response = raw_response.split("```")[1].strip()

      res_json = json.loads(raw_response)
      moteur = res_json.get("moteur", "mermaid")
      visual_payload = res_json.get("payload", "")
      
      # Nettoyage de sécurité du payload (retrait backticks markdown)
      if isinstance(visual_payload, str):
        visual_payload = re.sub(r"```[a-z]*\n?", "", visual_payload)
        visual_payload = visual_payload.replace("```", "").strip()

      explication = res_json.get("explication", "Visualisation générée.")

      await events.status(f"🎨 Interface {moteur.upper()} prête ({niveau_cognitif}).", done=True)
      response = EchoRichUI.generate_rich_view(moteur=moteur, payload=visual_payload, title=f"Rendu Visuel : {intention}")
      return response, wrap_tool_output(text=f"Le système Rendu Visuel a déployé une interface '{moteur}' ({niveau_cognitif}) : {explication}")

    except Exception as e:
      return wrap_tool_output(text=f"❌ Erreur Rendu Visuel: Le niveau {niveau_cognitif} est indisponible ou a rencontré une erreur technique : {str(e)}", status={"status": "error"})
