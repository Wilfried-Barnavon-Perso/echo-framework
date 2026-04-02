"""
title: ECHO Auth Service
author: Wilfried BARNAVON
version: 1.5
description: 1.5: Concurrent validation of all provided API keys before saving.
"""

import time
import orjson as std_json
import pybase64 as base64
import hashlib
import secrets
import re
import sys
import asyncio
from typing import Optional, Tuple, Any, List

import httpx

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth

from echo_constants import (
    GOOGLE_API_BASE_URL,
    ECHO_USER_AGENT,
    GOOGLE_API_KEY_PATTERN,
    GOOGLE_AI_STUDIO_WEB_URL
)

class AuthService:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.base_url = GOOGLE_API_BASE_URL
        self.echo_auth = EchoAuth(user_id=user_id)

    def get_auth_prompt(self) -> str:
        """Retourne le message d'instruction pour la configuration de la clé API."""
        return (
            f"🔐 **Configuration ECHO Requise**\n\n"
            f"ECHO supporte désormais **deux clés d'API** Google AI Studio pour une résilience maximale.\n\n"
            f"1. [Créez vos clés API ici]({GOOGLE_AI_STUDIO_WEB_URL})\n"
            f"2. Copiez une ou deux clés (commençant par `AIza...`).\n"
            f"3. Collez-les simplement ici, séparées par un espace ou un saut de ligne.\n\n"
            f"*Note : La deuxième clé servira de secours automatique en cas de surcharge de la première (ex: clé gratuite vs clé payante).* \n\n"
            f"*(ECHO_SESSION_AUTH_PENDING)*"
        )

    async def validate_and_save_api_key(self, raw_input: str) -> Tuple[bool, str]:
        """Extrait, vérifie la validité de TOUTES les clés et les enregistre si elles sont valides."""
        
        # Extraction de toutes les clés valides (separateurs: espace, tab, \n)
        keys = re.findall(GOOGLE_API_KEY_PATTERN, raw_input)
        
        if not keys:
            return False, "Aucune clé API valide n'a été détectée dans votre message."
            
        # Limiter à 2 clés maximum (Primaire et Secondaire)
        keys = keys[:2]

        async def _test_key(client: httpx.AsyncClient, key: str, index: int) -> Tuple[int, bool, str]:
            test_url = f"{GOOGLE_API_BASE_URL}/models?key={key}"
            try:
                resp = await client.get(test_url, headers={"User-Agent": ECHO_USER_AGENT}, timeout=10)
                if resp.status_code == 200:
                    return index, True, "OK"
                else:
                    return index, False, f"Rejetée par Google (HTTP {resp.status_code})"
            except Exception as e:
                return index, False, f"Erreur réseau ({str(e)})"

        # Handshake technique avec Google AI Studio (Test de toutes les clés en parallèle)
        try:
            async with httpx.AsyncClient() as client:
                tasks = [_test_key(client, k, i) for i, k in enumerate(keys)]
                results = await asyncio.gather(*tasks)
                
                # Vérification des résultats
                errors = []
                for idx, is_valid, reason in results:
                    if not is_valid:
                        errors.append(f"Clé n°{idx+1} invalide : {reason}")
                
                if errors:
                    err_msg = "Échec de validation.\n" + "\n".join(errors) + "\nVeuillez corriger et resaisir l'ensemble de vos clés."
                    return False, err_msg

                # Si on arrive ici, TOUTES les clés (1 ou 2) sont valides
                self.echo_auth.save_api_key('google_api_key', keys[0])
                
                if len(keys) > 1:
                    self.echo_auth.save_api_key('google_api_key_secondary', keys[1])
                else:
                    self.echo_auth.delete_api_key('google_api_key_secondary')
                    
                # Nettoyage des anciens tokens OAuth pour éviter les conflits
                self.echo_auth.delete_api_key('google_token')
                self.echo_auth.delete_api_key('google_project_id')
                
                msg = "Succès." if len(keys) == 1 else f"Succès. {len(keys)} clés valides enregistrées (1 principale + {len(keys)-1} secours)."
                return True, msg
                
        except Exception as e:
            return False, f"Erreur de connexion lors du handshake global : {str(e)}"
