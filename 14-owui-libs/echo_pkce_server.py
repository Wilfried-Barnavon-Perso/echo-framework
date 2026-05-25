"""
title: ECHO PKCE Callback Server
author: Wilfried BARNAVON
version: 1.0
description: Serveur de callback OAuth2 asyncio TCP pour le flow PKCE ECHO.
             Extrait et isolé depuis echo_auth.py.
             Écoute sur localhost:{port} — accessible uniquement via tunnel SSH.
             Place le code reçu dans une asyncio.Queue pour le pipe background task.
"""

import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("echo.pkce_server")


class EchoPKCECallbackServer:
    """
    Serveur TCP asyncio minimaliste pour recevoir le redirect OAuth2 de Google.

    Écoute sur localhost uniquement — jamais sur 0.0.0.0.
    Accessible uniquement via le tunnel SSH authentifié.
    """

    def __init__(self):
        self._server:         Optional[asyncio.Server] = None
        self._code_queue:     Optional[asyncio.Queue]  = None
        self._expected_state: str = ""
        self._callback_port:  int = 0

    async def start(self, callback_port: int, expected_state: str) -> asyncio.Queue:
        """
        Démarre le serveur TCP sur localhost:{callback_port}.
        Retourne la Queue dans laquelle sera déposé le code OAuth2 ou une erreur.
        Usage : code_or_error = await queue.get()
        """
        self._callback_port  = callback_port
        self._expected_state = expected_state
        self._code_queue     = asyncio.Queue(maxsize=1)

        self._server = await asyncio.start_server(
            self._handle_callback,
            "127.0.0.1",       # localhost uniquement — pas 0.0.0.0
            callback_port,
        )
        logger.info(f"[ECHO PKCE] Serveur callback démarré sur localhost:{callback_port}")
        return self._code_queue

    async def stop(self) -> None:
        """Arrête proprement le serveur TCP."""
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
            logger.info(f"[ECHO PKCE] Serveur callback arrêté (port {self._callback_port}).")

    # ------------------------------------------------------------------
    # Handler interne
    # ------------------------------------------------------------------

    async def _handle_callback(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        Parse la requête HTTP GET de Google.
        Format : GET /callback?code=4/0AX...&state=abc HTTP/1.1
        """
        try:
            raw = await asyncio.wait_for(reader.read(8192), timeout=15)
            request_line = raw.decode(errors="replace").split("\r\n")[0]
            logger.debug(f"[ECHO PKCE] Requête reçue : {request_line[:120]}")

            body, status_code = self._parse_request(request_line)

            response = (
                f"HTTP/1.1 {status_code}\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode() + body

            writer.write(response)
            await writer.drain()

        except asyncio.TimeoutError:
            logger.warning("[ECHO PKCE] Timeout lecture requête callback.")
        except Exception as e:
            logger.error(f"[ECHO PKCE] Erreur handler callback : {e}")
        finally:
            writer.close()

    def _parse_request(self, request_line: str) -> tuple:
        """
        Extrait code + state de la request line.
        Retourne (body_html: bytes, http_status: str).
        Dépose dans la Queue : le code (str) ou "ERROR:..." (str).
        """
        _STYLE = (
            "font-family:'Segoe UI',sans-serif;"
            "display:flex;flex-direction:column;align-items:center;"
            "justify-content:center;height:100vh;margin:0;"
            "background:#0f0f0f;color:#e0e0e0;"
        )

        # Requête non-GET ou chemin inconnu
        if "GET" not in request_line or "/callback" not in request_line:
            return b"<html><body>ECHO OAuth Callback</body></html>", "200 OK"

        try:
            path = request_line.split(" ")[1]
        except IndexError:
            return b"<html><body>Bad request</body></html>", "400 Bad Request"

        qs    = parse_qs(urlparse(path).query)
        code  = qs.get("code",  [None])[0]
        state = qs.get("state", [None])[0]
        error = qs.get("error", [None])[0]

        # Erreur Google
        if error:
            err_desc = qs.get("error_description", [""])[0]
            self._safe_put(f"ERROR:google:{error}:{err_desc}")
            body = (
                f"<html><body style='{_STYLE}'>"
                f"<h2>❌ Erreur Google</h2>"
                f"<p>{error}: {err_desc}</p>"
                f"<p>Retournez dans ECHO et réessayez.</p>"
                f"</body></html>"
            ).encode()
            return body, "400 Bad Request"

        # State invalide (protection CSRF)
        if state != self._expected_state:
            logger.warning(f"[ECHO PKCE] State mismatch — possible CSRF. Reçu: {state!r}")
            self._safe_put("ERROR:state_mismatch")
            body = (
                f"<html><body style='{_STYLE}'>"
                f"<h2>⚠️ Session invalide</h2>"
                f"<p>State non reconnu. Relancez l'authentification dans ECHO.</p>"
                f"</body></html>"
            ).encode()
            return body, "400 Bad Request"

        # Code valide
        if code:
            self._safe_put(code)
            body = (
                f"<html><body style='{_STYLE}'>"
                f"<h2>✅ Autorisation accordée !</h2>"
                f"<p>ECHO finalise la configuration en arrière-plan.</p>"
                f"<p style='color:#888'>Vous pouvez fermer cet onglet et votre terminal.</p>"
                f"<script>setTimeout(()=>window.close(),3000)</script>"
                f"</body></html>"
            ).encode()
            return body, "200 OK"

        # Aucun paramètre utile
        return b"<html><body>Parametres manquants.</body></html>", "400 Bad Request"

    def _safe_put(self, value: str) -> None:
        """Dépose dans la Queue sans bloquer (ignore si pleine)."""
        try:
            self._code_queue.put_nowait(value)
        except asyncio.QueueFull:
            logger.warning("[ECHO PKCE] Queue pleine — code ignoré.")
