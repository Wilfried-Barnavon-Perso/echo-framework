"""
title: Bypass RAG (Audit Aligned - Root Key Only)
author: ECHO Architecture
version: 1.9
description: Version finale v1.9. Validée par tests de production (Audit Aligned). Utilise la clé racine 'raw_files_from_filter' pour garantir le transport sécurisé des fichiers vers le Pipe.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging

# Configuration du log
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0, description="Priorité d'exécution."
        )

    def __init__(self):
        # 1. Empêche OWUI de lancer le moteur RAG/Tika
        self.file_handler = True
        # 2. Permet l'activation/désactivation globale dans l'UI
        self.toggle = True
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        # Si le filtre est désactivé via le toggle UI, on ne fait rien
        if not self.toggle:
            return body

        logger.info(f"🛡️ [Bypass RAG v1.9] Inlet triggered.")
        
        # --- PHASE 1 : SCAN STANDARD (Aligné sur l'hypothèse simplifiée) ---
        all_files = []
        seen_ids = set()

        # Helper pour ajouter proprement
        def add_file(f_obj: Any):
            if not isinstance(f_obj, dict): return
            
            target = f_obj
            if f_obj.get("type") == "file" and isinstance(f_obj.get("file"), dict):
                target = f_obj["file"]
            
            fid = target.get("id")
            if fid and fid not in seen_ids:
                all_files.append(target)
                seen_ids.add(fid)

        # Source A : body['files'] (Standard moderne)
        for f in body.get("files", []): add_file(f)
        
        # Source B : body['metadata']['files'] (Standard legacy/compatible)
        for f in body.get("metadata", {}).get("files", []): add_file(f)

        # Note: On ne scanne PAS 'messages' ici, conformément à votre directive 
        # d'éviter la multiplication des points de recherche non prouvés.

        # --- PHASE 2 : ACTION ROOT KEY (Recommandation Audit) ---
        if all_files:
            logger.info(f"🛡️ [Bypass RAG] {len(all_files)} fichiers détectés. Transfert vers raw_files_from_filter.")
            
            # Injection à la racine pour survivre au middleware OWUI
            body["raw_files_from_filter"] = all_files
            
            # --- PHASE 3 : NETTOYAGE ---
            if "files" in body: body["files"] = []
            if "metadata" in body and "files" in body["metadata"]: body["metadata"]["files"] = []
            
        else:
            logger.info("🛡️ [Bypass RAG] Aucun fichier trouvé (Scan Standard).")

        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body