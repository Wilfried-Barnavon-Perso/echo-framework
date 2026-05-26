"""
title: Export PDF Conversation
author: Wilfried BARNAVON
version: 1.9
description: 1.9: Fix CSS corrompu — les commentaires JS dans un tableau join() produisaient
             des valeurs 'undefined' dans le CSS final, cassant les sélecteurs @media print.
             Ajout de **kwargs dans la signature action() pour compatibilité OWUI.
             1.8: Suppression détection Same-Origin, iframes rendues nativement par le browser.
             1.7: Fix page blanche — pattern Path-Marking.
             1.6: classList pattern sans mutation d'ID React.
             1.5: window.print() natif, zéro dépendance CDN.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xNCAySDZhMiAyIDAgMCAwLTIgMnYxNmEyIDIgMCAwIDAgMiAyaDEyYTIgMiAwIDAgMCAyLTJWOHoiLz48cG9seWxpbmUgcG9pbnRzPSIxNCAyIDE0IDggMjAgOCIvPjxsaW5lIHgxPSIxMiIgeTE9IjEyIiB4Mj0iMTIiIHkyPSIxOCIvPjxwb2x5bGluZSBwb2ludHM9IjkgMTUgMTIgMTggMTUgMTUiLz48L3N2Zz4=
"""

import sys
import logging
from typing import Optional, Any
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _js_print_conversation() -> str:
    """Isolation d'impression via CSS Path-Marking + window.print() natif.

    Principe :
    - Aucune manipulation JS des iframes Rich Embeds.
    - window.print() délègue entièrement le rendu des iframes au navigateur.
      Si 'Autoriser même origine dans l'iframe sandbox' est actif dans OWUI,
      les schémas s'impriment. Sinon le navigateur laisse la zone en blanc —
      comportement attendu, sans code de détection fragile.

    CSS Pattern Path-Marking :
    - On remonte la chaîne body→...→chatContainer et on marque chaque ancêtre
      avec .echo-print-ancestor.
    - On cache UNIQUEMENT les frères de cette chaîne (pas les ancêtres).
    - display:none sur body>* bloquerait les descendants (règle CSS) → évité.
    """
    # CSS @media print injecté dans le DOM.
    # Chaque ligne est un élément string dans un array JS joint par '\n'.
    # ATTENTION : PAS de commentaires JS entre les éléments — ils produiraient
    # des valeurs undefined dans le array, corrompant le CSS lors du join.
    return r"""
return new Promise(function(resolve) {

    var STYLE_ID = 'echo-print-isolation-css';

    /* ── 1. Localisation du conteneur de conversation ────────────────────── */
    var chatContainer = document.querySelector('#chat-messages')
        || document.querySelector('[id*="messages-container"]')
        || document.querySelector('main .overflow-y-auto')
        || document.querySelector('main');

    if (!chatContainer) {
        resolve({ success: false, error: 'Conteneur de conversation introuvable' });
        return;
    }

    /* ── 2. CSS d'isolation — Pattern Path-Marking ───────────────────────── */
    /* Frères des ancêtres → cachés. Ancêtres → transparents. Cible → visible. */
    var printStyle = document.createElement('style');
    printStyle.id = STYLE_ID;
    printStyle.textContent =
        '@media print {\n' +
        '  body.echo-printing > *:not(.echo-print-ancestor):not(.echo-print-target) {\n' +
        '    display: none !important;\n' +
        '  }\n' +
        '  .echo-print-ancestor > *:not(.echo-print-ancestor):not(.echo-print-target) {\n' +
        '    display: none !important;\n' +
        '  }\n' +
        '  .echo-print-ancestor {\n' +
        '    display: block !important;\n' +
        '    position: static !important;\n' +
        '    overflow: visible !important;\n' +
        '    height: auto !important;\n' +
        '    max-height: none !important;\n' +
        '    width: 100% !important;\n' +
        '    background: transparent !important;\n' +
        '    padding: 0 !important;\n' +
        '    margin: 0 !important;\n' +
        '    border: none !important;\n' +
        '    box-shadow: none !important;\n' +
        '  }\n' +
        '  .echo-print-target {\n' +
        '    display: block !important;\n' +
        '    position: static !important;\n' +
        '    width: 100% !important;\n' +
        '    height: auto !important;\n' +
        '    max-height: none !important;\n' +
        '    overflow: visible !important;\n' +
        '    padding: 0 !important;\n' +
        '    margin: 0 !important;\n' +
        '  }\n' +
        '  .echo-print-target * {\n' +
        '    overflow: visible !important;\n' +
        '    max-height: none !important;\n' +
        '  }\n' +
        '  .echo-print-target iframe {\n' +
        '    overflow: visible !important;\n' +
        '    max-height: none !important;\n' +
        '  }\n' +
        '  @page { margin: 15mm; }\n' +
        '}';
    document.head.appendChild(printStyle);

    /* ── 3. Path-Marking : marquer ancêtres + cible ──────────────────────── */
    var ancestors = [];
    var ancestor = chatContainer.parentElement;
    while (ancestor && ancestor !== document.body) {
        ancestor.classList.add('echo-print-ancestor');
        ancestors.push(ancestor);
        ancestor = ancestor.parentElement;
    }
    document.body.classList.add('echo-printing');
    chatContainer.classList.add('echo-print-target');

    /* ── 4. Nettoyage après impression ───────────────────────────────────── */
    var resolved = false;
    function cleanup(outcome) {
        if (resolved) return;
        resolved = true;
        document.body.classList.remove('echo-printing');
        chatContainer.classList.remove('echo-print-target');
        ancestors.forEach(function(a) { a.classList.remove('echo-print-ancestor'); });
        var styleEl = document.getElementById(STYLE_ID);
        if (styleEl) styleEl.remove();
        resolve(outcome);
    }

    window.addEventListener('afterprint', function onAfterPrint() {
        window.removeEventListener('afterprint', onAfterPrint);
        cleanup({ success: true });
    });

    /* Fallback : résolution après 60s si afterprint absent */
    setTimeout(function() {
        cleanup({ success: true, timeout: true });
    }, 60000);

    /* ── 5. Impression ───────────────────────────────────────────────────── */
    window.print();
});
"""


# ==============================================================================
# CLASSE ACTION OWUI
# ==============================================================================

class Action:
    """Action ECHO — Export PDF de la conversation via impression native du navigateur.

    Workflow :
    1. Isole le conteneur #chat-messages avec le pattern CSS Path-Marking.
    2. Appelle window.print() → boîte de dialogue système du navigateur.
    3. L'utilisateur sélectionne 'Enregistrer en PDF'.
    4. Nettoyage DOM via l'événement afterprint.

    Iframes Rich Embeds :
    - Aucune manipulation JS. Le navigateur les rend nativement.
    - Activer 'Autoriser même origine dans l'iframe sandbox' dans
      Settings → Interface pour les inclure dans le PDF.
    """

    class Valves(BaseModel):
        priority: int = Field(
            default=4,
            description="Priorité d'affichage (4 = Dernier, après Replay Web)."
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
            await events.toast("❌ Export PDF : __event_call__ indisponible.", "error")
            return None

        await events.status(
            "🖨️ Ouverture de la boîte d'impression — sélectionnez 'Enregistrer en PDF'…",
            False
        )
        await events.toast(
            "🖨️ Dans la boîte de dialogue : sélectionnez 'Enregistrer en PDF' "
            "puis cliquez sur Enregistrer.",
            "info"
        )

        result = await __event_call__({
            "type": "execute",
            "data": {"code": _js_print_conversation()}
        })

        if not isinstance(result, dict):
            logger.error(f"[PDF-EXPORT] Résultat JS non reçu : {result!r}")
            await events.status("❌ L'export n'a pas retourné de résultat.", True)
            await events.toast("❌ Export PDF : résultat JS non reçu (voir logs).", "error")
            return None

        if result.get("success"):
            await events.status("✅ Impression terminée.", True)
        else:
            error_msg = result.get("error", "Erreur inconnue")
            logger.error(f"[PDF-EXPORT] Échec : {error_msg}")
            await events.status("❌ Échec export PDF.", True)
            await events.toast(f"❌ Erreur : {error_msg}", "error")

        return None
