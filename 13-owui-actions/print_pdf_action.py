"""
title: Print / PDF
author: Wilfried BARNAVON
version: 2.4
description: Outil d'exportation de la conversation courante vers un document PDF formaté pour l'impression.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xNCAySDZhMiAyIDAgMCAwLTIgMnYxNmEyIDIgMCAwIDAgMiAyaDEyYTIgMiAwIDAgMCAyLTJWOHoiLz48cG9seWxpbmUgcG9pbnRzPSIxNCAyIDE0IDggMjAgOCIvPjxsaW5lIHgxPSIxMiIgeTE9IjEyIiB4Mj0iMTIiIHkyPSIxOCIvPjxwb2x5bGluZSBwb2ludHM9IjkgMTUgMTIgMTggMTUgMTUiLz48L3N2Zz4=
"""
# Historique des versions :
# 2.4: Mise à jour de la priorité d'affichage à 100.
# 2.3: Factorisation de la logique CSS Path-Marking vers echo_ui.py (EchoUI.get_print_isolation_js). Renommage export_pdf -> print_pdf pour refléter le comportement natif.

import sys
import logging
from typing import Optional, Any
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents
from echo_ui import EchoUI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ==============================================================================
# CLASSE ACTION OWUI
# ==============================================================================

class Action:
    """Action ECHO — Impression et Export PDF de la conversation via impression native du navigateur.

    Workflow :
    1. Isole le conteneur cible avec le pattern CSS Path-Marking via EchoUI.
    2. Appelle window.print() → boîte de dialogue système du navigateur.
    3. L'utilisateur choisit l'imprimante ou 'Enregistrer en PDF'.
    4. Nettoyage DOM via l'événement afterprint.
    """

    class Valves(BaseModel):
        priority: int = Field(
            default=100,
            description="Priorité d'affichage (100 = Tout à la fin)."
        )

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        **kwargs,
    ) -> Optional[dict]:

        events = EchoEvents(__event_emitter__, __event_call__)

        if not __event_call__:
            await events.toast("❌ Print / PDF : __event_call__ indisponible.", "error")
            return None

        await events.status(
            "🖨️ Ouverture de la boîte d'impression...",
            False
        )
        await events.toast(
            "🖨️ Dans la boîte de dialogue : choisissez votre imprimante ou 'Enregistrer en PDF'.",
            "info"
        )

        js_logic = EchoUI.get_print_isolation_js('#chat-messages, [id*="messages-container"], main .overflow-y-auto, main')

        result = await __event_call__({
            "type": "execute",
            "data": {"code": js_logic}
        })

        if not isinstance(result, dict):
            logger.error(f"[PRINT-PDF] Résultat JS non reçu : {result!r}")
            await events.status("❌ L'action n'a pas retourné de résultat.", True)
            await events.toast("❌ Print / PDF : résultat JS non reçu (voir logs).", "error")
            return None

        if result.get("success"):
            await events.status("✅ Impression terminée.", True)
        else:
            error_msg = result.get("error", "Erreur inconnue")
            logger.error(f"[PRINT-PDF] Échec : {error_msg}")
            await events.status("❌ Échec de l'action.", True)
            await events.toast(f"❌ Erreur : {error_msg}", "error")

        return None
