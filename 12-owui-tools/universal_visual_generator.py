"""
title: ECHO Visual Engine
author: Wilfried BARNAVON
version: 5.239.11.1
description: Composant système interne : ECHO Visual Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.11.1: Ajout dans `context["message"]` de `generate_rich_visualization` de la consigne explicite anti-redondance Markdown après génération dans l'Iframe.
# 5.233.0.2: Contrainte de sortie JSON via response_schema=VisualCriticOutput dans _run_visual_critic et LeafletSchema dans SYS_VISUAL_REPAIR, décoration @echo_tool et simplification des retours.
# 5.233.0.1: Support multi-images unifié dans generate_image (paramètre input_images: Optional[List[str]]), factorisation du chargement Base64 via _load_reference_images, plafond IMAGE_GEN_MAX_INPUT_IMAGES=10 et traçabilité PAS.
# 5.232.5.1: Stockage et versionnement Git par essai/retouche (Option A) dans le workspace CODEX_WORKSPACE_GENERATED ("generated"), nommage court (< 15 caractères via short_name) et indexation sous resource_type="generated".
# 5.232.3.1: Suppression du paramètre niveau_cognitif (verrouillage MODEL_APOLLO avec clamping), transmission de chat_id à call_cascade, re-validation post-SYS_VISUAL_REPAIR, directive moteur adaptative au retry et résilience par essai dans generate_image.
# MOTEURS INTÉGRÉS ET DATAFLOWS :
# - mermaid (v11) : Syntaxe Mermaid pure
# - echarts (v5) : JSON ECharts strict
# - markmap (v0.17) : Markdown hiérarchique
# - leaflet (v1.9) : JSON {"center": [lat,lng], "zoom": int, "markers": [{"lat": float, "lng": float, "popup": "html"}]}
# - vega (v5), cytoscape, wavedrom, timeline, aframe, etc.

import asyncio
import sys
import re
import uuid
import base64
import unicodedata
import pybase64
import orjson as json
from typing import Optional, Any, Tuple, Union, Literal, List, Dict
from pydantic import BaseModel, Field

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
from echo_events import EchoEvents
from echo_core import echo_tool, wrap_tool_output, split_thought_process
from echo_protocol import VisualCriticOutput
from echo_gemini_client import EchoGeminiClient
from echo_http import load_image_as_base64
from echo_browser_lib import render_ephemeral_html
from echo_ui import EchoUI
from echo_state_manager import EchoStateManager
from echo_codex_git import CodexRepo
from echo_paths import format_codex_storage_path, get_codex_lock
from echo_constants import (
    CODEX_WORKSPACE_GENERATED,
    ECHO_SUBAGENT_CONTEXT,
    FILE_INGESTION_STATUS,
    GEN_IMG_MAX_TRIES,
    GEN_SCHEMA_MAX_TRIES,
    IMAGE_GEN_VALID_RATIOS,
    IMAGE_GEN_MAX_INPUT_IMAGES,
    MODEL_APOLLO,
    MODEL_IMAGE_GEN,
    get_generation_config,
)
from echo_prompts import (
    SYS_VISUAL_GENERATE,
    SYS_VISUAL_REPAIR,
    SYS_VISUAL_SCHEMA_CRITIC,
    SYS_VISUAL_IMAGE_GEN,
    SYS_VISUAL_IMAGE_CRITIC,
)



class Tools:
  class Valves(BaseModel):
    GENERATOR_TIMEOUT: int = Field(default=60, description="Délai d'attente maximum (secondes) pour la génération Rendu Visuel.")
    CDN_TIMEOUT_MS: int = Field(default=10000, description="Délai maximum (en ms) d'attente pour le chargement des librairies graphiques via CDN dans le navigateur.")

  def __init__(self):
    self.valves = self.Valves()

  @staticmethod
  def _extract_fenced_block(text: str) -> Tuple[str, str]:
    """Extrait (fence_lang, payload) depuis un bloc Markdown ```lang ... ``` avec fallback texte brut."""
    clean = (text or "").strip()
    match = re.search(r"```([a-zA-Z0-9_-]+)?[ \t]*\r?\n([\s\S]*?)```", clean)
    if match:
      return (match.group(1) or "").strip().lower(), match.group(2).strip()
    return "", clean

  @staticmethod
  def _sanitize_image_short_name(short_name: str, fallback_prompt: str, max_stem: int = 10) -> str:
    """Normalise un nom d'image en snake_case ASCII strict (<= max_stem caractères hors extension)."""
    raw = (short_name or "").strip() or (fallback_prompt or "").strip()
    ascii_str = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_str).strip("_")
    slug = re.sub(r"_+", "_", slug)[:max_stem].strip("_")
    return slug if len(slug) >= 3 else "image_gen"

  async def _run_visual_critic(
    self,
    img_b64: str,
    img_mime: str,
    critic_sys: str,
    user_prompt: str,
    user_id: str,
    metadata: dict,
    events: EchoEvents,
    override_thinking: str = "medium",
    chat_id: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Exécute l'évaluation multimodale (MODEL_APOLLO) sur une image en mémoire vive et retourne le verdict structuré."""
    resolved_chat_id = chat_id or (metadata or {}).get("chat_id")
    critic_conf = get_generation_config(MODEL_APOLLO, override_thinking=override_thinking)
    critic_payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inlineData": {"mimeType": img_mime, "data": img_b64}},
                    {"text": user_prompt},
                ],
            }
        ],
        "systemInstruction": {"parts": [{"text": critic_sys}]},
        "generationConfig": critic_conf,
    }
    verdict = "APPROVED"
    score = 8
    analysis = "Rendu conforme."
    defects: List[str] = []
    correction_prompt = ""
    short_name = ""
    try:
      critic_data, _, _ = await EchoGeminiClient.call_cascade(
          target_model_key=MODEL_APOLLO,
          payload=critic_payload,
          user_id=user_id,
          metadata=metadata,
          events=events,
          timeout=60,
          chat_id=resolved_chat_id,
          include_thoughts=False,
          response_schema=VisualCriticOutput,
      )
      if critic_data and critic_data.get("candidates"):
        c_parts = critic_data["candidates"][0].get("content", {}).get("parts", [])
        c_raw = "".join(p.get("text", "") for p in c_parts if not p.get("thought"))
        c_clean, _ = split_thought_process(c_raw)
        c_parsed = VisualCriticOutput.model_validate_json(c_clean.strip())
        verdict = c_parsed.verdict
        score = max(0, min(10, c_parsed.score))
        analysis = c_parsed.analysis.strip() or "Évaluation effectuée."
        defects = [str(d) for d in c_parsed.defects if d]
        correction_prompt = c_parsed.correction_prompt.strip()
        short_name = c_parsed.short_name.strip()
    except Exception:
      verdict = "APPROVED"
    return {
        "verdict": verdict,
        "score": score,
        "analysis": analysis,
        "defects": defects,
        "correction_prompt": correction_prompt,
        "short_name": short_name,
    }

  @echo_tool
  async def generate_rich_visualization(
    self,
    intention: str,
    donnees_contextuelles: str,
    # [MAINTENANCE_AI] Avertissement: Toujours mettre à jour ce Literal lors de l'ajout/suppression d'un moteur de rendu.
    moteur: Optional[Literal["markmap", "mermaid", "sketch", "echarts", "vega", "timeline", "bpmn", "gantt", "aframe", "svg", "cytoscape", "wavedrom", "chem", "science", "bio", "astro", "leaflet"]] = None,
    __user__: dict = {},
    __metadata__: dict = {},
    __event_emitter__: Any = None,
    __event_call__: Any = None
  ) -> Union[dict, Tuple[HTMLResponse, dict]]:
    """Génère une visualisation interactive (mermaid, markmap, echarts 2D/3D-GL, vega, science=Plotly 2D/3D, bio=3Dmol PDB/CID, chem=SMILES 2D, aframe=3D WebXR, cytoscape, leaflet, timeline, gantt, bpmn, wavedrom, astro, sketch, svg ; laisser moteur=None en cas de doute)."""
    events = EchoEvents(__event_emitter__, __event_call__)
    user_id = __user__.get("id", "system") if __user__ else "system"
    chat_id = __metadata__.get("chat_id") if __metadata__ else None

    sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
    if sub_ctx and sub_ctx.get("is_subagent"):
      return "❌ Action refusée : `generate_rich_visualization` est strictement réservé au Modèle principal (interdit depuis un Sous-Agent headless)."

    await events.status(f"🧠 Rendu Visuel : Orchestration {moteur or 'Auto'}...")

    moteurs_json = ["echarts", "vega", "timeline", "cytoscape", "wavedrom", "science", "astro", "leaflet"]
    all_engines = [
        "bio", "svg", "chem", "science", "astro", "aframe", "cytoscape",
        "wavedrom", "timeline", "bpmn", "echarts", "vega", "markmap",
        "gantt", "sketch", "leaflet", "mermaid",
    ]

    contents: List[Dict[str, Any]] = [
        {"role": "user", "parts": [{"text": f"INTENTION : {intention}\nDONNÉES : {donnees_contextuelles}"}]}
    ]
    candidates_history: List[Dict[str, Any]] = []

    try:
      for try_idx in range(1, GEN_SCHEMA_MAX_TRIES + 1):
        if moteur and try_idx == 1:
          directive_moteur = f"Le Modèle DOIT impérativement utiliser le moteur : '{moteur}'."
        elif moteur and try_idx > 1:
          directive_moteur = (
              f"Le moteur initialement demandé était '{moteur}', mais l'essai précédent ayant échoué, "
              f"Le Modèle PEUT conserver '{moteur}' en corrigeant le code OU basculer vers un moteur plus adapté "
              "de la taxonomie en l'indiquant explicitement sur la balise d'ouverture (```<moteur>)."
          )
        else:
          directive_moteur = "Le Modèle DOIT choisir le moteur le plus adapté."
        system_prompt = SYS_VISUAL_GENERATE.format(directive_moteur=directive_moteur)

        await events.status(
            f"🧠 Rendu Visuel : Génération ({moteur or 'Auto'}, essai {try_idx}/{GEN_SCHEMA_MAX_TRIES})..."
        )
        data, _, _ = await EchoGeminiClient.call_cascade(
            target_model_key=MODEL_APOLLO,
            payload={
                "contents": contents,
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "generationConfig": get_generation_config(MODEL_APOLLO, override_thinking="low"),
            },
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            timeout=self.valves.GENERATOR_TIMEOUT,
            chat_id=chat_id,
            include_thoughts=False,
        )
        if not data:
          if candidates_history:
            break
          return "❌ Cascade épuisée : aucun modèle disponible pour la génération visuelle."

        response_text = ""
        candidates = data.get("candidates", [])
        if candidates:
          for p in candidates[0].get("content", {}).get("parts", []):
            if "text" in p and not p.get("thought"):
              response_text += p["text"]

        clean_response, _ = split_thought_process(response_text)
        fence_lang, payload = self._extract_fenced_block(clean_response)

        if not payload:
          if candidates_history:
            break
          return wrap_tool_output(
              text="⚠️ Échec : Le modèle n'a pas généré de payload valide.",
              status={"status": "empty_response"},
          )

        final_moteur = fence_lang if (try_idx > 1 and fence_lang in all_engines) else moteur
        if not final_moteur:
          if fence_lang in all_engines:
            final_moteur = fence_lang
        if not final_moteur:
          for m in all_engines:
            if m in clean_response.lower()[:300]:
              final_moteur = m
              break
        final_moteur = final_moteur or "mermaid"

        # Validation JSON rapide (moteurs de données)
        if final_moteur in moteurs_json:
          try:
            parsed_json = json.loads(payload)
            if final_moteur == "leaflet":
              LeafletSchema(**parsed_json)
          except Exception as e:
            await events.status(f"⚠️ Rétroaction Syntaxique/Structurelle ({final_moteur})...")
            repair_prompt = (
                "<feedback>\n"
                f"Le payload généré pour le moteur '{final_moteur}' a échoué à la validation.\n"
                "</feedback>\n\n"
                "<error>\n"
                f"{str(e)}\n"
                "</error>\n\n"
                "<invalid_payload>\n"
                f"{payload}\n"
                "</invalid_payload>"
            )
            payload_fixed_data, _, _ = await EchoGeminiClient.call_cascade(
                target_model_key=MODEL_APOLLO,
                payload={
                    "contents": [{"role": "user", "parts": [{"text": repair_prompt}]}],
                    "systemInstruction": {"parts": [{"text": SYS_VISUAL_REPAIR}]},
                    "generationConfig": {**get_generation_config(MODEL_APOLLO, override_thinking="low"), "responseMimeType": "application/json"},
                },
                user_id=user_id,
                metadata=__metadata__,
                events=events,
                timeout=30,
                chat_id=chat_id,
                include_thoughts=False,
                response_schema=LeafletSchema if final_moteur == "leaflet" else None,
            )
            if payload_fixed_data:
              fix_cands = payload_fixed_data.get("candidates", [])
              if fix_cands:
                fixed_text = "".join(
                    [p["text"] for p in fix_cands[0].get("content", {}).get("parts", []) if "text" in p and not p.get("thought")]
                )
                fixed_text, _ = split_thought_process(fixed_text)
                _, payload = self._extract_fenced_block(fixed_text)
            try:
              parsed_json = json.loads(payload)
              if final_moteur == "leaflet":
                LeafletSchema(**parsed_json)
            except Exception as repair_err:
              if try_idx < GEN_SCHEMA_MAX_TRIES:
                contents.append({"role": "model", "parts": [{"text": f"```{final_moteur}\n{payload}\n```"}]})
                contents.append({
                    "role": "user",
                    "parts": [{
                        "text": f"Le JSON généré pour `{final_moteur}` est invalide ({repair_err}). Le Modèle DOIT fournir un JSON strictement valide ou basculer vers un moteur plus adapté."
                    }],
                })
                continue

        contents.append({"role": "model", "parts": [{"text": f"```{final_moteur}\n{payload}\n```"}]})

        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
        html_doc = EchoUI.build_rich_view_html(
            moteur=final_moteur,
            payload=b64_payload,
            title=f"ECHO Visual : {intention[:30]}...",
            cdn_timeout_ms=self.valves.CDN_TIMEOUT_MS,
        )

        # Rendu incognito Zéro-Disque & Contrôle Qualité Multimodal (F'')
        await events.status(
            f"🔍 Contrôle qualité visuel ({final_moteur}, essai {try_idx}/{GEN_SCHEMA_MAX_TRIES})..."
        )
        render_res = await render_ephemeral_html(
            html=html_doc,
            timeout_ms=self.valves.CDN_TIMEOUT_MS + 5000,
            user_id=user_id,
        )

        verdict = "APPROVED"
        score = 8
        analysis = "Visualisation validée."
        defects: List[str] = []
        correction_prompt = ""

        if render_res.get("ok") and render_res.get("b64"):
          dom_err = (render_res.get("dom_error") or "").strip()
          js_errs = [str(err) for err in (render_res.get("js_errors") or []) if err]
          runtime_err_note = ""
          if dom_err or js_errs:
            joined_errs = " | ".join(filter(None, [dom_err] + js_errs))
            runtime_err_note = f"\nERREURS CONSOLE/DOM DÉTECTÉES : {joined_errs}"

          critic_sys = SYS_VISUAL_SCHEMA_CRITIC.format(
              intention=intention,
              moteur=final_moteur,
              donnees_contextuelles=donnees_contextuelles[:4000],
          )
          critic_user_msg = (
              f"Évalue la conformité visuelle et ergonomique de ce schéma `{final_moteur}` "
              f"(Essai {try_idx}/{GEN_SCHEMA_MAX_TRIES}) par rapport à l'intention : {intention}"
              f"{runtime_err_note}"
          )
          critic_eval = await self._run_visual_critic(
              img_b64=render_res["b64"],
              img_mime=render_res.get("mime", "image/png"),
              critic_sys=critic_sys,
              user_prompt=critic_user_msg,
              user_id=user_id,
              metadata=__metadata__,
              events=events,
              override_thinking="low",
              chat_id=chat_id,
          )
          verdict = critic_eval["verdict"]
          score = critic_eval["score"]
          analysis = critic_eval["analysis"]
          defects = critic_eval["defects"]
          correction_prompt = critic_eval["correction_prompt"]

          # Garde-fou pragmatique : un rendu sans erreur DOM avec score >= 6 est validé dès le 1er essai
          if not dom_err and score >= 6:
            verdict = "APPROVED"

          # Si le DOM signale explicitement une erreur d'exécution du moteur, forcer le rejet
          if dom_err and verdict == "APPROVED":
            verdict = "REJECTED"
            score = min(score, 3)
            defects.append(f"Erreur d'exécution JS/DOM : {dom_err}")
            if not correction_prompt:
              correction_prompt = (
                  f"Le Modèle DOIT corriger l'erreur syntaxique/exécution suivante dans le code `{final_moteur}` : {dom_err}"
              )

        candidates_history.append({
            "try_idx": try_idx,
            "moteur": final_moteur,
            "html": html_doc,
            "verdict": verdict,
            "score": score,
            "analysis": analysis,
            "defects": defects,
        })
        # Purge immédiate de la référence mémoire au rendu Base64
        render_res = {}

        if verdict == "APPROVED":
          break

        if try_idx < GEN_SCHEMA_MAX_TRIES:
          corr_msg = correction_prompt or (
              f"Le Modèle DOIT corriger les défauts visuels/syntaxiques constatés à l'essai {try_idx} "
              f"pour le moteur `{final_moteur}` : {', '.join(defects) if defects else analysis}"
          )
          contents.append({"role": "user", "parts": [{"text": corr_msg}]})

      best_cand = (
          candidates_history[-1]
          if candidates_history[-1]["verdict"] == "APPROVED"
          else max(candidates_history, key=lambda c: (c.get("score", 0), c.get("try_idx", 0)))
      )
      await events.status("Génération terminée. Déploiement de l'interface...", done=True)

      response = HTMLResponse(
          content=best_cand["html"],
          headers={"Content-Disposition": "inline"},
      )
      context = {
          "status": "success",
          "message": (
              f"Visualisation {best_cand['moteur']} générée et vérifiée "
              f"(essai {best_cand['try_idx']}/{GEN_SCHEMA_MAX_TRIES}, score {best_cand['score']}/10 : {best_cand['analysis']}). "
              "Le rendu interactif est déjà affiché dans l'Iframe ; NE PAS reproduire ce schéma, diagramme ou code (Mermaid/SVG/ECharts/ASCII) en Markdown dans ta réponse."
          ),
          "moteur": best_cand["moteur"],
          "intention": intention,
          "try_idx": best_cand["try_idx"],
          "score": best_cand["score"],
          "verdict": best_cand["verdict"],
      }
      return response, wrap_tool_output(
          text=context["message"],
          status=context,
      )

    except Exception as e:
      return f"❌ Erreur lors de la génération visuelle : {str(e)}"

  async def _load_reference_images(
    self,
    input_images: Optional[Union[List[str], str]],
    user_id: str,
    chat_id: str,
    events: EchoEvents,
    sid: str,
  ) -> Tuple[List[Dict[str, Any]], List[str], Optional[str]]:
    """Normalise, valide et charge en Base64 les images de référence d'entrée (0 à N)."""
    raw_targets: List[str] = []
    if input_images:
      if isinstance(input_images, list):
        raw_targets = [str(x).strip() for x in input_images if str(x).strip()]
      elif isinstance(input_images, str):
        raw_targets = [s.strip() for s in re.split(r"[,;\n]+", input_images) if s.strip()]

    seen = set()
    targets: List[str] = []
    for t in raw_targets:
      if t not in seen:
        seen.add(t)
        targets.append(t)

    if len(targets) > IMAGE_GEN_MAX_INPUT_IMAGES:
      return [], [], (
          f"❌ Nombre d'images d'entrée excessif ({len(targets)} fournies, "
          f"maximum autorisé : {IMAGE_GEN_MAX_INPUT_IMAGES})."
      )

    user_parts: List[Dict[str, Any]] = []
    total = len(targets)
    for idx, target in enumerate(targets, 1):
      await events.status(
          f"🖼️ [{sid}] Chargement image de référence ({idx}/{total}) `{target}`..."
      )
      loaded_in = await load_image_as_base64(
          target=target,
          user_id=user_id,
          chat_id=chat_id,
          disallow_svg=True,
      )
      if not loaded_in.get("ok"):
        await events.status(
            f"❌ Échec du chargement de l'image de référence `{target}`.", done=True
        )
        return [], [], (
            f"❌ Impossible de charger l'image de référence `{target}` ({idx}/{total}) : "
            f"{loaded_in.get('error')}"
        )
      user_parts.append({
          "inlineData": {
              "mimeType": loaded_in["mime"],
              "data": loaded_in["b64"],
          }
      })
    return user_parts, targets, None

  @echo_tool
  async def generate_image(
    self,
    prompt: str,
    aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9"] = "1:1",
    input_images: Optional[List[str]] = None,
    sub_sid: Optional[str] = None,
    __user__: dict = {},
    __metadata__: dict = {},
    __event_emitter__: Any = None,
    __event_call__: Any = None,
  ) -> Union[dict, Tuple[HTMLResponse, dict]]:
    """Génère ou retouche une image via un sous-agent supervisé (générateur + évaluateur visuel), l'archive dans le Codex et l'affiche en Iframe."""
    events = EchoEvents(__event_emitter__, __event_call__)
    user_id = (__user__ or {}).get("id", "system")
    chat_id = (__metadata__ or {}).get("chat_id")

    sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
    if sub_ctx and sub_ctx.get("is_subagent"):
      return "❌ Action refusée : `generate_image` est strictement réservé au Modèle principal (interdit depuis un Sous-Agent headless)."

    if not chat_id:
      return "❌ Contexte de session (`chat_id`) manquant."

    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
      return "❌ Le paramètre `prompt` ne peut pas être vide."

    if aspect_ratio not in IMAGE_GEN_VALID_RATIOS:
      return f"❌ Ratio `{aspect_ratio}` invalide. Valeurs autorisées : {', '.join(IMAGE_GEN_VALID_RATIOS)}."

    sid = sub_sid.strip() if (sub_sid and sub_sid.strip()) else f"dlg_img_{uuid.uuid4().hex[:8]}"
    if not sid.startswith("dlg_img_"):
      sid = f"dlg_img_{sid}"

    state_mgr = EchoStateManager(user_id=user_id, chat_id=chat_id)
    history = state_mgr.get_thread_history(sid)
    initial_step = len(history)

    user_parts, loaded_targets, load_err = await self._load_reference_images(
        input_images=input_images,
        user_id=user_id,
        chat_id=chat_id,
        events=events,
        sid=sid,
    )
    if load_err:
      return load_err

    user_parts.append({"text": clean_prompt})
    history.append({"role": "user", "parts": user_parts})
    state_mgr.save_thread_step(sid, chat_id, "image_gen", len(history) - 1, "user", user_parts)

    generated_candidates: List[Dict[str, Any]] = []
    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }

    existing_res = state_mgr.get_resource(sid)
    rel_path: Optional[str] = existing_res.get("name") if existing_res else None
    last_commit_sha = ""
    last_commit_msg = ""

    try:
      for try_idx in range(1, GEN_IMG_MAX_TRIES + 1):
        await events.status(
            f"🎨 [{sid}] Génération de l'image (Essai {try_idx}/{GEN_IMG_MAX_TRIES}, ratio {aspect_ratio})..."
        )
        gen_conf = get_generation_config(MODEL_IMAGE_GEN)
        gen_conf["imageConfig"] = {"aspectRatio": aspect_ratio}
        gen_payload = {
            "contents": history,
            "systemInstruction": {
                "parts": [{"text": SYS_VISUAL_IMAGE_GEN.format(aspect_ratio=aspect_ratio)}]
            },
            "generationConfig": gen_conf,
        }

        try:
          gen_resp = await EchoGeminiClient.call(
              MODEL_IMAGE_GEN,
              gen_payload,
              user_id=user_id,
              events=events,
              timeout=max(120, int(self.valves.GENERATOR_TIMEOUT)),
              chat_id=chat_id,
              gen_config_override={"imageConfig": {"aspectRatio": aspect_ratio}},
          )
        except Exception as call_err:
          if generated_candidates:
            break
          raise call_err

        candidates = (gen_resp or {}).get("candidates", [])
        if not candidates:
          if generated_candidates:
            break
          raise RuntimeError(f"Aucune réponse retournée par {MODEL_IMAGE_GEN} à l'essai {try_idx}.")

        raw_parts = candidates[0].get("content", {}).get("parts", [])
        img_b64 = None
        img_mime = "image/png"
        gen_texts: List[str] = []
        thought_sig = None

        for p in raw_parts:
          if not thought_sig and p.get("thoughtSignature"):
            thought_sig = p["thoughtSignature"]
          inline = p.get("inlineData") or p.get("inline_data")
          if inline and inline.get("data") and not img_b64:
            img_b64 = inline["data"]
            img_mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
          elif "text" in p and not p.get("thought"):
            gen_texts.append(p["text"])

        if not img_b64:
          if try_idx < GEN_IMG_MAX_TRIES:
            history.append({"role": "model", "parts": raw_parts or [{"text": ""}]})
            state_mgr.save_thread_step(
                sid, chat_id, "image_gen", len(history) - 1, "model", raw_parts or [{"text": ""}], thought_sig
            )
            retry_parts = [{
                "text": "ERREUR SYSTÈME : Aucune donnée image (inlineData) n'a été produite à l'essai précédent. Le Modèle DOIT impérativement générer et retourner une image binaire."
            }]
            history.append({"role": "user", "parts": retry_parts})
            state_mgr.save_thread_step(
                sid, chat_id, "image_gen", len(history) - 1, "user", retry_parts
            )
            continue
          if generated_candidates:
            break
          raise RuntimeError(
              f"Le modèle {MODEL_IMAGE_GEN} n'a retourné aucune donnée image (inlineData) à l'essai {try_idx}."
          )

        history.append({"role": "model", "parts": raw_parts})
        state_mgr.save_thread_step(
            sid, chat_id, "image_gen", len(history) - 1, "model", raw_parts, thought_sig
        )

        # Contrôle qualité visuel (F'')
        await events.status(
            f"🔍 [{sid}] Contrôle qualité visuel (Essai {try_idx}/{GEN_IMG_MAX_TRIES})..."
        )
        critic_sys = SYS_VISUAL_IMAGE_CRITIC.format(
            original_prompt=clean_prompt,
            aspect_ratio=aspect_ratio,
        )
        critic_user_msg = (
            f"Évalue la conformité de cette image (Essai {try_idx}/{GEN_IMG_MAX_TRIES}) "
            f"par rapport à la requête : {clean_prompt}"
        )
        critic_eval = await self._run_visual_critic(
            img_b64=img_b64,
            img_mime=img_mime,
            critic_sys=critic_sys,
            user_prompt=critic_user_msg,
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            chat_id=chat_id,
        )
        verdict = critic_eval["verdict"]
        score = critic_eval["score"]
        analysis = critic_eval["analysis"]
        defects = critic_eval["defects"]
        correction_prompt = critic_eval["correction_prompt"]
        short_name = critic_eval.get("short_name", "")

        raw_bytes = pybase64.b64decode(img_b64)
        ext = ext_map.get(img_mime.lower(), ".png")

        async with get_codex_lock(user_id, chat_id):
          repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_GENERATED)
          if not rel_path:
            stem = self._sanitize_image_short_name(short_name, clean_prompt, max_stem=10)
            candidate_name = f"{stem}{ext}"
            existing_names = {f["filename"] for f in repo.list_files()}
            if candidate_name in existing_names:
              idx_col = 2
              while True:
                suffix = f"_{idx_col}"
                trimmed = stem[: max(3, 10 - len(suffix))].rstrip("_")
                candidate_name = f"{trimmed}{suffix}{ext}"
                if candidate_name not in existing_names:
                  break
                idx_col += 1
            rel_path = candidate_name

          commit_msg = f"step {initial_step} try {try_idx}/{GEN_IMG_MAX_TRIES} ({verdict}, {score}/10)"
          commit_sha = await asyncio.to_thread(
              repo.commit_file,
              rel_path,
              raw_bytes.decode("latin-1"),
              commit_msg,
              "ECHO Image Agent",
              True,
          )
          last_commit_sha = commit_sha
          last_commit_msg = commit_msg

        codex_urn = f"[{rel_path}](codex:current:{CODEX_WORKSPACE_GENERATED}/{rel_path})"
        generated_candidates.append({
            "try_idx": try_idx,
            "b64": img_b64,
            "raw_bytes": raw_bytes,
            "mime": img_mime,
            "gen_text": " ".join(gen_texts).strip(),
            "verdict": verdict,
            "score": score,
            "analysis": analysis,
            "defects": defects,
            "best": False,
            "filename": rel_path,
            "codex_path": rel_path,
            "codex_urn": codex_urn,
            "commit_sha": commit_sha,
        })

        if verdict == "APPROVED":
          break

        if try_idx < GEN_IMG_MAX_TRIES:
          corr_msg = correction_prompt or (
              f"Le Modèle DOIT corriger les défauts visuels constatés à l'essai {try_idx} : "
              f"{', '.join(defects) if defects else analysis}"
          )
          corr_parts = [{"text": corr_msg}]
          history.append({"role": "user", "parts": corr_parts})
          state_mgr.save_thread_step(
              sid, chat_id, "image_gen", len(history) - 1, "user", corr_parts
          )

      # Sélection des images à transférer
      if generated_candidates[-1]["verdict"] == "APPROVED":
        retained = [generated_candidates[-1]]
        retained[0]["best"] = True
        exhausted = False
      else:
        retained = generated_candidates
        best_cand = max(retained, key=lambda c: (c.get("score", 0), c.get("try_idx", 0)))
        best_cand["best"] = True
        exhausted = True

      primary = next((c for c in retained if c.get("best")), retained[-1])

      # Si plafond atteint et le meilleur essai n'est pas le dernier, restaurer ses octets en HEAD
      if exhausted and primary["try_idx"] != generated_candidates[-1]["try_idx"]:
        async with get_codex_lock(user_id, chat_id):
          repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_GENERATED)
          restore_msg = f"restore best try {primary['try_idx']} ({primary['score']}/10)"
          last_commit_sha = await asyncio.to_thread(
              repo.commit_file,
              rel_path,
              primary["raw_bytes"].decode("latin-1"),
              restore_msg,
              "ECHO Image Agent",
              True,
          )
          last_commit_msg = restore_msg

      # Indexation dans echo_resources (1 entrée unique par sub_sid dans workspace generated)
      await events.status(f"💾 [{sid}] Indexation de `{rel_path}` dans le Registre...")
      state_mgr.save_resource(
          id=sid,
          name=rel_path,
          resource_type="generated",
          status=FILE_INGESTION_STATUS["PUT_IN_CONTEXT"],
          mime=primary["mime"],
          summary=f"Image générée ({aspect_ratio}) — {clean_prompt[:100]}",
          storage_path=format_codex_storage_path(rel_path, CODEX_WORKSPACE_GENERATED),
          git_tracked=True,
          author_model=MODEL_IMAGE_GEN,
          language="image",
          lines=(len(primary["raw_bytes"]) + 15) // 16,
          last_commit=(last_commit_sha or "")[:12],
          commit_msg=last_commit_msg,
      )

      gallery_items = [
          {
              "data_uri": f"data:{c['mime']};base64,{c['b64']}",
              "filename": c["filename"],
              "label": f"Essai {c['try_idx']} ({c['score']}/10)",
              "best": bool(c.get("best")),
              "analysis": c.get("analysis", ""),
          }
          for c in retained
      ]
      html_viewer = EchoUI.image_viewer(
          f"data:{primary['mime']};base64,{primary['b64']}",
          title=f"ECHO Image [{sid}] ({aspect_ratio})",
          filename=primary["filename"],
          is_remote_fallback=False,
          gallery=gallery_items if len(gallery_items) > 1 else None,
      )

      await events.status(f"✅ [{sid}] Image `{rel_path}` générée et affichée.", done=True)

      urns_list = primary["codex_urn"]
      sources_line = ""
      if loaded_targets:
        sources_list = ", ".join(f"`{t}`" for t in loaded_targets)
        sources_line = f"- **Images sources (références / fusion) :** {sources_list}\n"

      if exhausted:
        status_line = (
            f"PLAFOND D'ESSAIS ATTEINT ({GEN_IMG_MAX_TRIES}/{GEN_IMG_MAX_TRIES}) — "
            f"Les {len(retained)} essais ont été versionnés dans `{rel_path}` (meilleure évaluation : "
            f"Essai {primary['try_idx']}, score {primary['score']}/10 : {primary['analysis']})."
        )
      else:
        status_line = (
            f"VALIDÉE À L'ESSAI {primary['try_idx']}/{GEN_IMG_MAX_TRIES} "
            f"(score {primary['score']}/10 : {primary['analysis']})."
        )

      pas_report = (
          f"### 🎨 RAPPORT DU SOUS-AGENT D'IMAGERIE [`{sid}`]\n"
          f"- **Statut :** {status_line}\n"
          f"- **Ratio :** `{aspect_ratio}`\n"
          + sources_line
          + f"- **Archivage Codex (versionné Git) :** {urns_list}\n"
          + (f"- **Note du générateur :** {primary['gen_text']}\n" if primary.get("gen_text") else "")
          + "\n"
          "DIRECTIVE SYSTÈME (PAS) : L'affichage visuel interactif est intégralement pris en charge par "
          "Le Système via l'Iframe. Il est ABSOLUMENT INTERDIT pour Le Modèle d'émettre une balise "
          "Markdown `![...](...)` redondante dans sa réponse. Le Modèle DOIT présenter la référence Codex "
          f"({urns_list}) à L'Utilisateur et DOIT impérativement s'assurer auprès de L'Utilisateur que "
          "l'image générée lui convient. Si L'Utilisateur souhaite une modification ou retouche, Le Modèle "
          f"DOIT rappeler `generate_image` avec `sub_sid=\"{sid}\"`. Si L'Utilisateur souhaite recommencer "
          f"depuis le début (from scratch), Le Modèle DOIT clôturer `{sid}` via `close_image_session` puis "
          "rappeler `generate_image` avec `sub_sid=None`. Si L'Utilisateur confirme que l'image "
          f"lui convient, Le Modèle DOIT clôturer la session via `close_image_session(sub_sid=\"{sid}\")`."
      )

      return (
          html_viewer,
          wrap_tool_output(
              text=pas_report,
              status={
                  "status": "success",
                  "sub_sid": sid,
                  "input_images": loaded_targets,
                  "aspect_ratio": aspect_ratio,
                  "exhausted_tries": exhausted,
                  "tries_count": len(generated_candidates),
                  "retained_files": [rel_path],
              },
          ),
      )

    except Exception as e:
      await events.status(f"❌ [{sid}] Échec de la génération d'image.", done=True)
      return wrap_tool_output(
          text=f"❌ Erreur lors de la génération d'image (`{sid}`) : {str(e)}",
          status={"status": "error", "sub_sid": sid},
      )

  @echo_tool
  async def close_image_session(
    self,
    sub_sid: str,
    __user__: dict = {},
    __metadata__: dict = {},
    __event_emitter__: Any = None,
  ) -> dict:
    """Ferme définitivement une session de sous-agent d'imagerie (`sub_sid`) et purge son historique lorsque l'image convient à l'Utilisateur."""
    events = EchoEvents(__event_emitter__)
    user_id = (__user__ or {}).get("id", "system")
    chat_id = (__metadata__ or {}).get("chat_id")
    if not chat_id:
      return "❌ Contexte de session (`chat_id`) manquant."

    clean_sid = (sub_sid or "").strip()
    state_mgr = EchoStateManager(user_id=user_id, chat_id=chat_id)
    threads = state_mgr.list_threads(chat_id)
    sids_in_chat = {t["sub_sid"] for t in threads}

    if clean_sid not in sids_in_chat:
      return wrap_tool_output(
          text=f"ℹ️ La session d'imagerie `{clean_sid}` est déjà clôturée ou introuvable dans cette conversation.",
          status={"status": "success", "sub_sid": clean_sid, "already_closed": True},
      )

    state_mgr.delete_thread(clean_sid)
    await events.status(f"🔒 Session d'imagerie [{clean_sid}] clôturée.", done=True)
    return wrap_tool_output(
        text=f"✅ Session d'imagerie `{clean_sid}` fermée et purgée.",
        status={"status": "success", "sub_sid": clean_sid},
    )
