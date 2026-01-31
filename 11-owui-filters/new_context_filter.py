"""
title: ECHO Context Filter
author: Wilfried BARNAVON
version: 1.16
description: 1.16: Changement de placeholder (%%ECHO_VERSION%%) pour éviter les conflits avec le template engine OWUI.
"""


from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import os
import json
import logging
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

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

        # Récupération des UserValves
        user_valves = __user__.get("valves") if __user__ else None
        
        # Valeurs par défaut si pas de valves injectées
        enable_user_name = False
        override_location = ""
        
        if user_valves:
            # Gestion robuste selon si c'est un objet ou un dict (selon version OWUI)
            try:
                enable_user_name = user_valves.ENABLE_USER_NAME
                override_location = user_valves.OVERRIDE_LOCATION
            except AttributeError:
                # Fallback si c'est un dict
                enable_user_name = user_valves.get("ENABLE_USER_NAME", False)
                override_location = user_valves.get("OVERRIDE_LOCATION", "")

        if self.valves.debug_context:
            logger.info(f"🛡️ [Filter v1.13] Processing Request... (User: {enable_user_name}, Loc: {override_location})")
        
        # ----------------------------------------------------------------------
        # MODULE 1 : BYPASS RAG (Gestion des fichiers)
        # ----------------------------------------------------------------------
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

        # Scan Standard (body.files) et Legacy (metadata.files)
        for f in (body.get("files") or []): add_file(f)
        metadata = body.get("metadata") or {}
        for f in (metadata.get("files") or []): add_file(f)

        if all_files:
            if self.valves.debug_context: 
                logger.info(f"📂 [Bypass RAG] {len(all_files)} fichiers déplacés vers 'raw_files_from_filter'.")
            body["raw_files_from_filter"] = all_files
            if "files" in body: body["files"] = []
            if "metadata" in body and "files" in body["metadata"]: body["metadata"]["files"] = []

        # ----------------------------------------------------------------------
        # MODULE 2 : CONTEXT OPTIMIZER (Split Static/Dynamic & Versioning)
        # ----------------------------------------------------------------------
        messages = body.get("messages", [])
        if messages:
            # 1. Récupération de la version
            echo_version = self._get_echo_version()
            
            # 2. Identification des messages
            system_msg_idx = -1
            last_user_msg_idx = -1

            for i, msg in enumerate(messages):
                role = msg.get("role")
                if role == "system":
                    system_msg_idx = i
                elif role == "user":
                    last_user_msg_idx = i # On garde le dernier user

            # 3. Traitement du System Prompt (si présent)
            env_block = None
            if system_msg_idx != -1:
                sys_content = messages[system_msg_idx].get("content", "")
                
                # A. Injection Version (Priorité Absolue - Manipulation Texte Simple)
                if "%%ECHO_VERSION%%" in sys_content:
                    sys_content = sys_content.replace("%%ECHO_VERSION%%", echo_version)
                    # On met à jour immédiatement pour garantir que c'est fait
                    messages[system_msg_idx]["content"] = sys_content
                    if self.valves.debug_context:
                        logger.info(f"🔄 [Context Optimizer] Version injectée (Raw Text): {echo_version}")

                # B. Optimisation Cache (Extraction Environnement via JSON)
                try:
                    # On tente de parser le contenu (potentiellement déjà modifié avec la version)
                    if isinstance(sys_content, str) and sys_content.strip().startswith("{"):
                        sys_json = json.loads(sys_content)
                        
                        # Extraction Environnement (Split Cache)
                        if "environnement_utilisateur" in sys_json:
                            env_block = sys_json.pop("environnement_utilisateur")
                            
                            # Application des UserValves sur l'environnement extrait
                            if env_block:
                                if not enable_user_name and "nom_utilisateur" in env_block:
                                    env_block["nom_utilisateur"] = "[Anonyme]"
                                if override_location and "lieu_utilisateur" in env_block:
                                    env_block["lieu_utilisateur"] = override_location

                            if self.valves.debug_context:
                                logger.info(f"✂️ [Context Optimizer] Environnement extrait (JSON).")
                        
                            # Mise à jour du System Message (JSON nettoyé et re-sérialisé)
                            messages[system_msg_idx]["content"] = json.dumps(sys_json, ensure_ascii=False)
                        
                except Exception as e:
                    # Si le parsing échoue, ce n'est pas grave pour la version (déjà faite),
                    # on perd juste l'optimisation du cache pour ce tour.
                    if self.valves.debug_context:
                        logger.warning(f"⚠️ [Context Optimizer] Skip optimisation cache (JSON error): {e}")

            # 4. Injection de l'Environnement dans le User Prompt
            if env_block and last_user_msg_idx != -1:
                user_content = messages[last_user_msg_idx].get("content", "")
                
                # Formatage Markdown JSON Context
                env_text = f"```json:context\n{{\"environnement_utilisateur\": {json.dumps(env_block, ensure_ascii=False, indent=2)}}}\n```\n\n"
                
                if isinstance(user_content, str):
                    messages[last_user_msg_idx]["content"] = env_text + user_content
                elif isinstance(user_content, list):
                    # Cas multimodal (liste de dicts text/image)
                    # On cherche le premier bloc texte pour préfixer
                    text_injected = False
                    for part in user_content:
                        if part.get("type") == "text":
                            part["text"] = env_text + part.get("text", "")
                            text_injected = True
                            break
                    if not text_injected:
                        # Si pas de texte, on l'ajoute au début
                        user_content.insert(0, {"type": "text", "text": env_text})
                
                if self.valves.debug_context:
                    logger.info(f"💉 [Context Optimizer] Environnement réinjecté dans le dernier message User.")

        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body