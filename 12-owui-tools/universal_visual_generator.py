"""
title: ECHO Universal Visual Generator
author: Wilfried BARNAVON
version: 3.1
description: 3.1: Intégration du moteur 'sketch' pour les diagrammes façon croquis à main levée.
"""

import os
import sys
import re
import base64
import orjson as json
from typing import Optional, Any, Tuple, Union
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_ui import EchoRichUI
from echo_constants import (
    ECHO_USER_AGENT, GOOGLE_API_BASE_URL, MODEL_FLASH, MODEL_PRO, MODEL_LITE, MODEL_ROUTING,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES
)

class Tools:
  class Valves(BaseModel):
    KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
    MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre total de tentatives avant d'abandonner.")
    GENERATOR_TIMEOUT: int = Field(default=60, description="Délai d'attente maximum (secondes) pour la génération Rendu Visuel.")

  def __init__(self):
    self.valves = self.Valves()
    self.auth = EchoAuth()

  async def generate_rich_visualization(
    self,
    intention: str,
    donnees_contextuelles: str,
    moteur: Optional[str] = None,
    niveau_cognitif: str = "MODEL_FLASH",
    __user__: dict = {},
    __event_emitter__: Any = None,
    __event_call__: Any = None
  ) -> Union[dict, Tuple[HTMLResponse, dict]]:
    """
    Déploie une interface visuelle interactive (OS de Rendu Visuel ECHO).
    MOTEURS DISPONIBLES :
    - 'markmap' : Mindmaps et hiérarchies (Markdown).
    - 'mermaid' : Flowcharts, Séquences, UML (Mermaid v11).
    - 'sketch' : Schémas et diagrammes façon "croquis à main levée" (utilise Mermaid + Rough.js).
    - 'echarts' : Tableaux de bord, Finance, Heatmaps (JSON).
    - 'vega' : Science des données, Statistiques complexes (JSON).
    - 'timeline' : Chronologies et frises historiques (JSON).
    - 'bpmn' : Processus métiers normés (ITIL, BPMN 2.0).
    - 'gantt' : Plannings et gestion de projet (JSON).
    - 'aframe' : Scènes 3D, Architecture, Volumes (HTML).
    - 'svg' : Plans 2D, Schémas vectoriels sur mesure.
    - 'leaflet' : Cartographie et données géographiques (JSON/GeoJSON).
    - 'cytoscape' : Réseaux, Graphes de force, Clusters (JSON).
    - 'smiles' : Structures moléculaires et chimie (SMILES).
    - 'wavedrom' : Électronique, Signaux numériques (JSON).
    
    :param intention: Description du besoin visuel (ex: "Plan 3D d'un bureau", "Structure de la molécule d'aspirine").
    :param donnees_contextuelles: Faits, chiffres et relations à modéliser.
    :param moteur: Optionnel. Force un moteur spécifique parmi la liste ci-dessus.
    :param niveau_cognitif: Choisir selon la complexité : 
        - 'MODEL_LITE' : Mindmaps simples, Flowcharts basiques.
        - 'MODEL_FLASH' : Standard (ECharts, BPMN, Leaflet, SMILES, SVG).
        - 'MODEL_PRO' : Complexe (3D A-Frame, Réseaux Cytoscape, Signaux WaveDrom, Vega) ou après échec de validation.
    """
    events = EchoEvents(__event_emitter__, __event_call__)
    await events.status(f"🧠 Rendu Visuel : Orchestration {moteur or 'Auto'}...")

    # 1. Auth
    api_keys = self.auth.get_api_keys(__user__.get("id"))
    if not api_keys: 
      return wrap_tool_output(text="❌ Configuration ECHO Requise : Aucune clé API trouvée.", status={"status": "error"})

    # 2. Manuel Technique de l'Architecte
    directive_moteur = f"Tu DOIS impérativement utiliser le moteur : '{moteur}'." if moteur else "Choisis le moteur le plus adapté."
    
    system_prompt = (
      "Tu es l'Architecte Visuel Souverain du Framework ECHO.\n"
      "Ta mission : transformer une intention et des données en un payload technique certifié.\n\n"
      f"{directive_moteur}\n\n"
      "MANUEL DE RÉFÉRENCE TECHNIQUE :\n"
      "1. 'markmap' : Markdown hiérarchique pur. Pas de code block.\n"
      "2. 'mermaid' : Syntaxe v11 stricte. Pas de backticks. Utilise 'flowchart TD' ou 'sequenceDiagram'. Thème sombre par défaut.\n"
      "3. 'echarts' : JSON ECharts 5+. Inclure 'tooltip', 'legend', 'xAxis', 'yAxis', 'series'. Style sombre.\n"
      "4. 'vega' : JSON Vega-Lite strict. Spécifier '$schema', 'data', 'mark', 'encoding'.\n"
      "5. 'timeline' : JSON TimelineJS. Structure : {'events': [{'start_date':..., 'text':{'headline':..., 'text':...}}]}.\n"
      "6. 'bpmn' : XML BPMN 2.0 valide (bpmn-js).\n"
      "7. 'gantt' : JSON Frappe Gantt. Liste d'objets : {'id':..., 'name':..., 'start':..., 'end':..., 'progress':...}.\n"
      "8. 'aframe' : Code HTML A-Frame (balises <a-scene>, <a-box>, etc.). Pas de scripts externes.\n"
      "9. 'svg' : Code SVG complet avec viewBox. Pas de scripts.\n"
      "10. 'leaflet' : JSON GeoJSON ou objet {'lat':..., 'lon':..., 'zoom':..., 'features':[...]}.\n"
      "11. 'cytoscape' : JSON Elements. Structure : {'nodes': [{'data':{'id':...}}], 'edges': [...]}.\n"
      "12. 'smiles' : Chaîne SMILES brute (ex: 'CC(=O)OC1=CC=CC=C1C(=O)O').\n"
      "13. 'wavedrom' : JSON WaveDrom. Structure : {'signal': [{'name':..., 'wave':...}]}.\n\n"
      "RÈGLE D'OR : Réponds EXCLUSIVEMENT par un objet JSON :\n"
      "{\n"
      " \"moteur\": \"nom_du_moteur\",\n"
      " \"payload\": \"...le code brut ou JSON...\",\n"
      " \"explication\": \"Justification technique pour ECHO\"\n"
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
    
    # 3. Délégation Cognitive
    target_model = MODEL_ROUTING.get(niveau_cognitif, niveau_cognitif)
    try:
      data = await EchoGeminiClient.call(
        keys=api_keys, target_model=target_model, payload=payload_request,
        threshold=self.valves.KEY_SWITCH_THRESHOLD, max_retries=self.valves.MAX_RETRIES,
        events=events, timeout=self.valves.GENERATOR_TIMEOUT
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
      moteur_final = res_json.get("moteur", "mermaid").lower()
      visual_payload = res_json.get("payload", "")
      explication = res_json.get("explication", "Visualisation générée.")

      # --- PHASE 5 : VALIDATION (OPTION A) ---
      moteurs_json = ["echarts", "vega", "timeline", "gantt", "leaflet", "cytoscape", "wavedrom"]
      if moteur_final in moteurs_json:
        try:
          if isinstance(visual_payload, str):
            json.loads(visual_payload)
          else:
            visual_payload = json.dumps(visual_payload).decode('utf-8')
        except Exception as je:
          return wrap_tool_output(
            text=f"❌ Erreur de Validation JSON pour '{moteur_final}' : {str(je)}. Merci de corriger la syntaxe et de relancer.",
            status={"status": "error"}
          )

      # Nettoyage des backticks pour les moteurs textuels
      if isinstance(visual_payload, str):
        visual_payload = re.sub(r"```[a-z]*\n?", "", visual_payload)
        visual_payload = visual_payload.replace("```", "").strip()

      # --- PHASE 3 : TUNNEL BASE64 ---
      if not isinstance(visual_payload, str):
        visual_payload = json.dumps(visual_payload).decode('utf-8')
      
      b64_payload = base64.b64encode(visual_payload.encode('utf-8')).decode('utf-8')

      await events.status(f"🎨 Interface {moteur_final.upper()} prête ({niveau_cognitif}).", done=True)
      html_resp = EchoRichUI.generate_rich_view(moteur=moteur_final, payload=b64_payload, title=f"Rendu Visuel : {intention}")
      
      return html_resp, wrap_tool_output(
          text=f"Le système Rendu Visuel a déployé une interface '{moteur_final}' ({niveau_cognitif}) : {explication}",
          status={"status": "success"}
      )

    except Exception as e:
      return wrap_tool_output(text=f"❌ Erreur Rendu Visuel: Le niveau {niveau_cognitif} a rencontré une erreur : {str(e)}", status={"status": "error"})

text=f"❌ Erreur Rendu Visuel: Le niveau {niveau_cognitif} a rencontré une erreur : {str(e)}", status={"status": "error"})

