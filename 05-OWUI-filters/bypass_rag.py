"""
title: Bypass RAG & Force Raw Metadata
author: ECHO Architecture
version: 1.1
description: Active file_handler=True ET déplace les fichiers vers metadata.raw_files pour une consommation parfaite par le Pipe Gemini 3.
"""

from pydantic import BaseModel, Field
from typing import Optional
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
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        logger.info(f"🛡️ [Bypass RAG] Inlet triggered.")
        
        # Récupération des fichiers depuis les différents endroits possibles
        files = body.get("files", [])
        
        # Vérification aussi dans body['metadata']['files'] (structure OWUI parfois changeante)
        meta_files = body.get("metadata", {}).get("files", [])
        
        all_files = []
        if files: all_files.extend(files)
        if meta_files: all_files.extend(meta_files)

        if all_files:
            logger.info(f"🛡️ [Bypass RAG] {len(all_files)} fichiers détectés -> Transfert vers raw_files.")
            
            # 2. Création de la clé attendue par ton Pipe Engine (Section 5, Logique B)
            if "metadata" not in body: body["metadata"] = {}
            body["metadata"]["raw_files"] = all_files
            
            # 3. Nettoyage (Optionnel mais recommandé pour être sûr qu'OWUI ne voit plus rien)
            # On laisse body['files'] vide pour le reste du pipeline OWUI standard, 
            # mais le Pipe Gemini lira 'raw_files'.
            # Note: Si tu as besoin que l'UI affiche les fichiers après coup, ne vide pas tout, 
            # mais pour le 'processing', c'est plus sûr.
            
        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body