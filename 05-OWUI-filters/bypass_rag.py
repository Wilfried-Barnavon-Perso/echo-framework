"""
title: Bypass RAG for Native Uploads
author: ECHO Architecture
version: 1.0
description: Active le mode 'file_handler = True' pour empêcher Open WebUI de traiter les fichiers (RAG/Extraction). Cela permet de transmettre les métadonnées brutes des fichiers (ID, Path) au Pipe suivant (Gemini) sans altération ni suppression en cas d'échec d'extraction.
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
        # C'est la ligne MAGIQUE.
        # Elle signale à OWUI : "Je gère les fichiers, ne lance pas le RAG."
        self.file_handler = True
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """
        Intercept la requête avant le traitement RAG.
        """
        # On log pour confirmer que le filtre est bien passé par là
        logger.info(f"🛡️ [Bypass RAG Filter] Inlet triggered.")
        
        # On vérifie si des fichiers sont présents
        messages = body.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if "files" in last_msg or body.get("files"):
                logger.info(f"🛡️ [Bypass RAG Filter] Fichiers détectés. RAG désactivé par file_handler=True.")
                # On ne touche à rien, on laisse passer le body tel quel.
                # Grâce à self.file_handler=True, OWUI ne va PAS essayer d'extraire le texte.
            
        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body