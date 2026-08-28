"""
title: ECHO TCP Keep-Alive Filter
author: ECHO Framework
author_url: https://github.com/echo-framework
version: 1.0
description: Composant système interne : Protection SPA contre le WAF (SvelteKit HTTP Keep-Alive).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.0: Création du filtre pour contrer le bug de hard reload (Tab Discarding / TCP timeout) de SvelteKit.

from pydantic import BaseModel, Field
from typing import Optional

class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0, 
            hidden=True,
            description="Priorité d'exécution (0 = premier)."
        )

    def __init__(self):
        self.valves = self.Valves()
        
    async def inlet(
        self,
        body: dict,
        __event_emitter__=None,
        __user__: Optional[dict] = None,
        __request__=None
    ) -> dict:
        """
        Intercepte la toute première requête entrante du chat.
        Injecte un Singleton JS léger pour pinger l'API et maintenir
        le pool de connexions HTTP (Keep-Alive) ouvert.
        """
        if not __event_emitter__:
            return body

        # Payload Javascript minimaliste et idempotent
        js_code = """
        (function initECHOKeepAlive() {
            if (window._echoTCPKeepAlive) return;
            window._echoTCPKeepAlive = true;
            
            console.log("🛡️ ECHO TCP Keep-Alive (WAF Protection) initié.");
            
            // Ping l'API toutes les 2 minutes (120000 ms) pour éviter
            // la déconnexion silencieuse du TCP par le WAF (proxy_read_timeout).
            setInterval(() => {
                fetch('/health').catch(() => {});
            }, 120000);
        })();
        """

        # Émission du code JS vers le DOM du navigateur via l'Event Emitter
        await __event_emitter__({
            "type": "execute",
            "data": {
                "code": js_code
            }
        })
        
        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body
