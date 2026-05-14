"""
title: ECHO Visual Engine
author: Wilfried BARNAVON
version: 5.0
description: 5.0: Restauration de la validation JSON stricte et support hybride Markdown/Technique.
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
from echo_utils import EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_ui import EchoUI
from echo_constants import (
    MODEL_FLASH, MODEL_ROUTING,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES
)

class Tools:
  class Valves(BaseModel):
    KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
    MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre total de tentatives avant d'abandonner.")
    GENERATOR_TIMEOUT: int = Field(default=60, description="Délai d'attente maximum (secondes) pour la génération Rendu Visuel.")
    CDN_TIMEOUT_MS: int = Field(default=10000, description="Délai maximum (en ms) d'attente pour le chargement des librairies graphiques via CDN dans le navigateur.")

  def __init__(self):
    self.valves = self.Valves()

  async def generate_rich_visualization(
    self,
    intention: str,
    donnees_contextuelles: str,
    moteur: Optional[str] = None,
    niveau_cognitif: str = "MODEL_FLASH",
    __user__: dict = {},
    __metadata__: dict = {},
    __event_emitter__: Any = None,
    __event_call__: Any = None
  ) -> Union[dict, Tuple[HTMLResponse, dict]]:
    """
    Déploie une interface visuelle interactive (OS de Rendu Visuel ECHO - Mermaid v11.14.0).
    MOTEURS DISPONIBLES :
    - 'markmap' : Mindmaps et hiérarchies (Markdown).
    - 'mermaid' : Flowcharts, Séquences, UML (Mermaid v11).
    - 'sketch' : Schémas et diagrammes façon "croquis à main levée" (utilise Mermaid + Rough.js).
    - 'echarts' : Tableaux de bord, Finance, Heatmaps, Cartes de données (JSON).
    - 'vega' : Science des données, Statistiques complexes (JSON).
    - 'timeline' : Chronologies et frises historiques (JSON).
    - 'bpmn' : Processus métiers normés (XML BPMN 2.0).
    - 'gantt' : Plannings et gestion de projet (SYNTAXE MERMAID GANTT).
    - 'aframe' : Scènes 3D, Architecture, Volumes (HTML).
    - 'svg' : Plans 2D, Schémas vectoriels sur mesure.
    - 'cytoscape' : Réseaux, Graphes de force, Clusters (JSON).
    - 'wavedrom' : Électronique, Signaux numériques (JSON).
    - 'chem' : Chimie moléculaire (SMILES).
    - 'science' : Physique & Mathématiques (Plotly.js JSON).
    - 'bio' : Biologie structurelle (ID PDB, ex: 1A8M).
    - 'astro' : Exploration céleste (D3-CELESTIAL JSON).
    
    :param intention: Description du besoin visuel (ex: "Plan 3D d'un bureau", "Structure de la hiérarchie").
    :param donnees_contextuelles: Faits, chiffres et relations à modéliser.
    :param moteur: Optionnel. Force un moteur spécifique parmi la liste ci-dessus.
    :param niveau_cognitif: Choisir selon la complexité : 
        - 'MODEL_LITE' : Mindmaps simples, Flowcharts basiques.
        - 'MODEL_FLASH' : Standard (ECharts, BPMN, SVG).
        - 'MODEL_PRO' : Complexe (3D A-Frame, Réseaux Cytoscape, Signaux WaveDrom, Vega).
    """
    events = EchoEvents(__event_emitter__, __event_call__)
    await events.status(f"🧠 Rendu Visuel : Orchestration {moteur or 'Auto'}...")
    user_id = __user__.get("id", "system") if __user__ else "system"

    # 1. Manuel Technique de l'Architecte
    directive_moteur = f"Tu DOIS impérativement utiliser le moteur : '{moteur}'." if moteur else "Choisis le moteur le plus adapté."
    
    system_prompt = (
      "Tu es l'Architecte Visuel Souverain du Framework ECHO.\n"
      "Ta mission : transformer une intention et des données en un payload technique certifié.\n\n"
      f"{directive_moteur}\n\n"
      "MANUEL DE RÉFÉRENCE TECHNIQUE (Thème Clair) :\n"
      "1. 'markmap' : Markdown hiérarchique pur. Pas de code block.\n"
      "2. 'mermaid' : Syntaxe v11 stricte. Pas de backticks. 'flowchart TD' ou 'sequenceDiagram'.\n"
      "3. 'echarts' : JSON ECharts 5+. Inclure 'tooltip', 'legend', 'xAxis', 'yAxis', 'series'. Thème clair.\n"
      "4. 'vega' : JSON Vega-Lite strict. Spécifier '$schema', 'data', 'mark', 'encoding'.\n"
      "5. 'timeline' : JSON TimelineJS. Structure : {'events': [{'start_date':..., 'text':{'headline':..., 'text':...}}]}.\n"
      "6. 'bpmn' : XML BPMN 2.0 valide.\n"
      "7. 'gantt' : Syntaxe Mermaid Gantt pure (commencer par 'gantt').\n"
      "8. 'aframe' : Code HTML A-Frame (balises <a-scene>, <a-box>, etc.).\n"
      "9. 'cytoscape' : JSON Cytoscape.js (elements: {nodes: [], edges: []}).\n"
      "10. 'wavedrom' : JSON WaveDrom (signal: []).\n"
      "11. 'astro' : JSON Celestial (projection: 'orthographic', transform: 'equatorial').\n"
      "12. 'bio' : Renvoie UNIQUEMENT l'ID PDB (ex: 1A8M) ou le contenu complet d'un fichier PDB.\n"
      "13. 'svg' : Code XML SVG complet et valide.\n"
      "14. 'chem' : Chaîne SMILES (ex: 'CC(=O)OC1=CC=CC=C1C(=O)O').\n"
      "15. 'science' : JSON Plotly.js (data: [], layout: {}).\n\n"
      "CONSIGNE CRITIQUE : Renvoie UNIQUEMENT le payload technique (JSON, XML ou Markdown) dans un bloc de code. "
      "N'ajoute aucun commentaire avant ou après."
    )

    # 3. Génération
    try:
      response_text = ""
      async for chunk in EchoGeminiClient.stream(
          target_model=MODEL_ROUTING.get(niveau_cognitif, MODEL_FLASH),
          payload={"contents": [{"role": "user", "parts": [{"text": f"INTENTION : {intention}\nDONNÉES : {donnees_contextuelles}"}]}],
                   "systemInstruction": {"parts": [{"text": system_prompt}]}},
          user_id=user_id,
          threshold=self.valves.KEY_SWITCH_THRESHOLD,
          max_retries=self.valves.MAX_RETRIES,
          events=events,
          timeout=self.valves.GENERATOR_TIMEOUT
      ):
          if isinstance(chunk, str): response_text += chunk
          elif isinstance(chunk, dict):
              target = chunk.get("response", {}) if "response" in chunk else chunk
              candidates = target.get("candidates", [])
              if candidates and candidates[0].get("content"):
                  for p in candidates[0]["content"].get("parts", []):
                      if "text" in p: response_text += p["text"]

      # Extraction du payload (Pattern Bloc de Code ou Texte Brut)
      payload = ""
      code_match = re.search(r"```(?:\w+)?\n?(.*?)\n?```", response_text, re.DOTALL)
      payload = code_match.group(1).strip() if code_match else response_text.strip()

      if not payload:
        return wrap_tool_output(text="⚠️ Échec : Le modèle n'a pas généré de payload valide.", status={"status": "empty_response"})

      # Détermination du moteur final
      final_moteur = moteur
      if not final_moteur:
        for m in ["bio", "svg", "chem", "science", "astro", "aframe", "cytoscape", "wavedrom", "timeline", "bpmn", "echarts", "vega", "markmap", "gantt", "sketch", "mermaid"]:
          if m in response_text.lower()[:300]:
            final_moteur = m
            break
      final_moteur = final_moteur or "mermaid"

      # --- VALIDATION JSON STRICTE (Moteurs Data) ---
      moteurs_json = ["echarts", "vega", "timeline", "cytoscape", "wavedrom", "science", "astro"]
      if final_moteur in moteurs_json:
          try:
              json.loads(payload)
          except Exception:
              await events.status(f"⚠️ Correction syntaxique JSON ({final_moteur})...")
              repair_prompt = f"Corrige ce JSON pour le moteur '{final_moteur}' pour qu'il soit valide et sans commentaires :\n\n{payload}"
              payload_fixed = await EchoGeminiClient.call_distillation(repair_prompt, __user__, __metadata__, is_json=False)
              if payload_fixed: payload = payload_fixed

      await events.status("Génération terminée. Déploiement de l'interface...", done=True)

      # 4. Rendu
      response, context = EchoUI.generate_rich_view(
        moteur=final_moteur,
        payload=base64.b64encode(payload.encode()).decode(),
        title=f"ECHO Visual : {intention[:30]}...",
        cdn_timeout_ms=self.valves.CDN_TIMEOUT_MS
      )
      
      context.update({"moteur": final_moteur, "intention": intention})
      return response, wrap_tool_output(text=context["message"], status=context)

    except Exception as e:
      return wrap_tool_output(text=f"❌ Erreur lors de la génération visuelle : {str(e)}", status={"status": "error"})
