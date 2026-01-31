"""
title: ECHO Context Filter
author: Wilfried BARNAVON
version: 1.17
description: 1.17: Optimisation Cache Ultime. Construction dynamique de l'environnement depuis __metadata__ et injection dans le message User. Prompt système devient 100% statique.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import os
import json
import logging
import datetime

# Configuration du log
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0, description="Priorité d'exécution."
        )
        debug_context: bool = Field(
            default=False, description="Afficher les logs de transformation du contexte."
        )

    class UserValves(BaseModel):
        ENABLE_USER_NAME: bool = Field(
            default=False, description="🔒 Partager nom d'utilisateur (Si OFF, le nom est masqué)"
        )
        OVERRIDE_LOCATION: str = Field(
            default="", description="✏️ Forcer Lieu (Surcharge tout)"
        )

    def __init__(self):
        # 1. Empêche OWUI de lancer le moteur RAG/Tika
        self.file_handler = True
        # 2. Permet l'activation/désactivation globale dans l'UI
        self.toggle = True
        self.valves = self.Valves()
        # Chemin vers le fichier de version (Monté par Docker)
        self.version_path = "/app/backend/data/ECHO_VERSION"

    def _get_echo_version(self) -> str:
        """Récupère la version dynamique ou une valeur par défaut."""
        try:
            # Priorité au fichier monté dans le conteneur
            if os.path.exists(self.version_path):
                with open(self.version_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            
            # Fallback développement local (dossier racine du projet)
            local_version = "VERSION"
            if os.path.exists(local_version):
                with open(local_version, 'r', encoding='utf-8') as f:
                    return f.read().strip()
                    
        except Exception as e:
            logger.error(f"⚠️ [Context Optimizer] Erreur lecture version: {e}")
        
        return "Unknown"

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        # Si le filtre est désactivé via le toggle UI, on ne fait rien
        if not self.toggle:
            return body

        # --- 1. Récupération Configuration Utilisateur ---
        user_valves = __user__.get("valves") if __user__ else None
        enable_user_name = False
        override_location = ""
        
        if user_valves:
            try:
                enable_user_name = user_valves.ENABLE_USER_NAME
                override_location = user_valves.OVERRIDE_LOCATION
            except AttributeError:
                enable_user_name = user_valves.get("ENABLE_USER_NAME", False)
                override_location = user_valves.get("OVERRIDE_LOCATION", "")

        # --- 2. Construction de l'Environnement Dynamique ---
        
        # A. Version
        echo_version = self._get_echo_version()
        
        # B. Utilisateur
        user_name = __user__.get("name", "Utilisateur") if __user__ else "Utilisateur"
        if not enable_user_name:
            user_name = "[Anonyme]"
            
        # C. Métadonnées (Date, Lieu) depuis Open WebUI
        meta_vars = {}
        if "metadata" in body and "variables" in body["metadata"]:
            meta_vars = body["metadata"]["variables"]
        
        # Récupération sécurisée des variables OWUI
        current_datetime = meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu")
        current_timezone = meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
        user_location = meta_vars.get("{{USER_LOCATION}}", "Inconnu")
        
        # Override Location
        if override_location:
            user_location = override_location

        # Construction du bloc JSON
        env_block = {
            "environnement_utilisateur": {
                "version_framework": echo_version,
                "nom_utilisateur": user_name,
                "date_et_heure": current_datetime,
                "timezone": current_timezone,
                "lieu_utilisateur": user_location
            }
        }

        if self.valves.debug_context:
            logger.info(f"🛡️ [Filter v1.17] Environment Built: {json.dumps(env_block)}")

        # --- 3. Gestion Bypass RAG (Files) ---
        all_files = []
        seen_ids = set()

        def add_file(f_obj: Any):
            if not isinstance(f_obj, dict): return
            target = f_obj
            if f_obj.get("type") == "file" and isinstance(f_obj.get("file"), dict):
                target = f_obj["file"]
            fid = target.get("id")
            if fid and fid not in seen_ids:
                all_files.append(target)
                seen_ids.add(fid)

        for f in (body.get("files") or []): add_file(f)
        metadata = body.get("metadata") or {}
        for f in (metadata.get("files") or []): add_file(f)

        if all_files:
            body["raw_files_from_filter"] = all_files
            if "files" in body: body["files"] = []
            if "metadata" in body and "files" in body["metadata"]: body["metadata"]["files"] = []

        # --- 4. Injection dans le Message Utilisateur ---
        messages = body.get("messages", [])
        if messages:
            last_user_msg_idx = -1
            for i, msg in enumerate(messages):
                if msg.get("role") == "user":
                    last_user_msg_idx = i
            
            if last_user_msg_idx != -1:
                user_content = messages[last_user_msg_idx].get("content", "")
                
                # Formatage Markdown JSON Context
                env_text = f"```json:context\n{json.dumps(env_block, ensure_ascii=False, indent=2)}\n```\n\n"
                
                if isinstance(user_content, str):
                    messages[last_user_msg_idx]["content"] = env_text + user_content
                elif isinstance(user_content, list):
                    text_injected = False
                    for part in user_content:
                        if part.get("type") == "text":
                            part["text"] = env_text + part.get("text", "")
                            text_injected = True
                            break
                    if not text_injected:
                        user_content.insert(0, {"type": "text", "text": env_text})
                
                if self.valves.debug_context:
                    logger.info(f"💉 [Context Optimizer] Contexte injecté dans le message User.")

        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body