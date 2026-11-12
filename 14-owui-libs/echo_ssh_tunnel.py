"""
title: ECHO SSH Tunnel Server
author: Wilfried BARNAVON
version: 6.5.0.1
description: Serveur SSH ephemere (asyncssh) pour le flow OAuth2 PKCE ECHO.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.5.0.1: Prise en charge des sessions terminales passives interactives pour l'authentification OAuth2 PKCE sans flag -N, notification temps réel et déconnexion RFC 4253 propre.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.0: Allocation dynamique dans une plage unique (bug : callback expose).
# 1.1: Separation en deux plages : ssh_range (Docker-expose) et
# callback_range (interne uniquement). Le port callback n'est
# accessible que via le tunnel SSH authentifie.

import asyncio
import logging
import os
import secrets
import socket
from typing import Optional, Tuple

logger = logging.getLogger("echo.ssh_tunnel")


# ---------------------------------------------------------------------------
# Utilitaires réseau
# ---------------------------------------------------------------------------

def find_free_port_in_range(start: int, end: int) -> int:
    """
    Trouve le premier port TCP libre dans [start, end] (bind sur 0.0.0.0).
    Lève RuntimeError si aucun port disponible.
    """
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"Aucun port libre dans la plage {start}-{end}. "
                       f"Trop d'authentifications simultanées ?")


def get_server_ip(request=None) -> str:
    """
    Détecte l'IP du serveur telle que vue par le client.
    Ordre de priorité :
      1. Header 'Host' de la requête HTTP (ce que le client a tapé)
      2. IP sortante du container (socket trick vers 8.8.8.8)
      3. Variable d'environnement ECHO_VM_IP
      4. 127.0.0.1
    """
    # 1. Header Host (le plus fiable — correspond à l'URL du navigateur)
    if request is not None:
        try:
            host = request.headers.get("host", "").split(":")[0].strip()
            if host and host not in ("localhost", "127.0.0.1", "::1", ""):
                return host
        except Exception:
            pass

    # 2. IP sortante du container
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # pas de paquet envoyé — juste le routing
            return s.getsockname()[0]
    except Exception:
        pass

    # 3. Env var
    env_ip = os.getenv("ECHO_VM_IP", "")
    if env_ip:
        return env_ip

    return "127.0.0.1"


# ---------------------------------------------------------------------------
# Serveur SSH éphémère
# ---------------------------------------------------------------------------

class EchoSSHTunnelServer:
    """
    Serveur SSH asyncssh minimaliste pour le tunnel OAuth2 PKCE.

    - Un seul utilisateur autorisé : ECHO_SSH_TUNNEL_USER
    - Mot de passe aléatoire jetable (secrets.token_urlsafe)
    - Clé hôte générée à la volée (non persistée)
    - Aucun shell, aucun exec — port forward localhost:callback_port uniquement
    - Auto-stop après `timeout` secondes
    """

    def __init__(
        self,
        ssh_range_start:      int,  # Ports Docker-exposes (client SSH s'y connecte)
        ssh_range_end:        int,
        callback_range_start: int,  # Ports internes (JAMAIS Docker-exposes)
        callback_range_end:   int,
        tunnel_user:          str = "echo-auth",
        timeout:              int = 120,
    ):
        self._ssh_start      = ssh_range_start
        self._ssh_end        = ssh_range_end
        self._cb_start       = callback_range_start
        self._cb_end         = callback_range_end
        self._tunnel_user    = tunnel_user
        self._timeout        = timeout

        self._temp_password: str              = ""
        self._ssh_port:      int              = 0
        self._callback_port: int              = 0
        self._server:        Optional[object] = None
        self._stop_task:     Optional[asyncio.Task] = None
        self._active_sessions: set = set()
        self._active_connections: set = set()

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    async def start(self, request=None) -> Tuple[str, int, int, str]:
        """
        Demarre le serveur SSH sur le premier port libre dans ssh_range.
        Robuste TOCTOU : tente directement asyncssh.create_server() sur chaque port
        de la plage SSH, sans pre-verification separee (pas de race condition).
        Retourne (server_ip, ssh_port, callback_port, temp_password).
        """
        try:
            import asyncssh  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "asyncssh non disponible. Verifiez ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=true "
                "dans Open WebUI et relancez le pipe."
            ) from e

        # Mot de passe jetable et cle hote (communs a toutes les tentatives)
        self._temp_password = secrets.token_urlsafe(6)
        host_key = asyncssh.generate_private_key("ssh-rsa")

        # Alloue le port callback separement (plage interne, non exposee)
        self._callback_port = find_free_port_in_range(self._cb_start, self._cb_end)

        # Fabrique la classe serveur (depend de callback_port, deja fixe)
        server_class = self._make_server_class()

        # Tente de binder asyncssh directement sur chaque port SSH disponible.
        # Pas de pre-verification separee -> pas de TOCTOU.
        last_err = None
        for ssh_port in range(self._ssh_start, self._ssh_end + 1):
            try:
                self._server = await asyncssh.create_server(
                    server_class,
                    host="0.0.0.0",
                    port=ssh_port,
                    server_host_keys=[host_key],
                    authorized_client_keys=None,
                )
                self._ssh_port = ssh_port
                break
            except OSError as e:
                last_err = e
                logger.debug(f"[ECHO SSH] Port {ssh_port} occupe, essai suivant...")
                continue
        else:
            raise RuntimeError(
                f"Aucun port SSH libre dans la plage {self._ssh_start}-{self._ssh_end}. "
                f"Trop d'authentifications simultanees ? (derniere erreur: {last_err})"
            )

        # Auto-stop apres timeout
        self._stop_task = asyncio.create_task(self._auto_stop())

        server_ip = get_server_ip(request)
        logger.info(
            f"[ECHO SSH] Serveur demarre — port SSH:{self._ssh_port} "
            f"callback:{self._callback_port} IP:{server_ip} TTL:{self._timeout}s"
        )
        return server_ip, self._ssh_port, self._callback_port, self._temp_password


    async def stop(self, message: str = "Serveur arrêté") -> None:
        """Arrête proprement le serveur SSH et annule l'auto-stop."""
        if self._stop_task and not self._stop_task.done():
            self._stop_task.cancel()

        status_msg = str(message or "Serveur arrêté")

        # 1. Notification et clôture des canaux terminaux actifs
        for session in list(self._active_sessions):
            try:
                session.close_with_message(status_msg)
            except Exception as e:
                logger.debug(f"[ECHO SSH] Erreur notification session : {e}")

        # Bref délai pour vider les tampons TCP vers le client (flush réseau)
        if self._active_sessions or self._active_connections:
            await asyncio.sleep(0.15)

        # 2. Déconnexion propre RFC 4253 des connexions actives
        for conn in list(self._active_connections):
            try:
                import asyncssh  # type: ignore
                disc_code = getattr(asyncssh, "DISC_BY_APPLICATION", 11)
                conn.disconnect(disc_code, status_msg)
            except Exception as e:
                try:
                    conn.close()
                except Exception:
                    pass
                logger.debug(f"[ECHO SSH] Erreur déconnexion conn : {e}")

        self._active_sessions.clear()
        self._active_connections.clear()

        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
            logger.info(
                f"[ECHO SSH] Serveur arrêté ({status_msg}) — ports {self._ssh_port}/{self._callback_port} libérés."
            )

    @property
    def is_active(self) -> bool:
        return self._server is not None

    @property
    def callback_port(self) -> int:
        return self._callback_port

    @property
    def ssh_port(self) -> int:
        return self._ssh_port

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _make_server_class(self):
        """Fabrique une classe SSHServer liée aux attributs de cette instance."""
        temp_password   = self._temp_password
        tunnel_user     = self._tunnel_user
        callback_port   = self._callback_port
        server_self     = self

        try:
            import asyncssh  # type: ignore
        except ImportError:
            raise RuntimeError("asyncssh requis.")

        class _EchoServerSession(asyncssh.SSHServerSession):
            def __init__(self):
                self._chan = None

            def connection_made(self, chan):
                self._chan = chan
                server_self._active_sessions.add(self)

            def shell_requested(self) -> bool:
                return True

            def exec_requested(self, command: str) -> bool:
                return True

            def session_started(self):
                if self._chan and not self._chan.is_closing():
                    self._chan.write(
                        "\r\n============================================================\r\n"
                        "  Connexion SSH établie, en attente de l'Authentification OAuth2...\r\n"
                        "============================================================\r\n"
                    )

            def data_received(self, data, datatype):
                pass  # Ignore les saisies clavier

            def close_with_message(self, msg: str):
                if self._chan and not self._chan.is_closing():
                    self._chan.write(f"\r\n{msg}\r\n")
                    try:
                        self._chan.exit(0)
                    except Exception:
                        pass
                    try:
                        self._chan.close()
                    except Exception:
                        pass

            def connection_lost(self, exc):
                server_self._active_sessions.discard(self)

        class _EchoSSHServer(asyncssh.SSHServer):
            def __init__(self):
                self._conn = None

            def connection_made(self, conn):
                self._conn = conn
                server_self._active_connections.add(conn)
                logger.debug(f"[ECHO SSH] Connexion entrante : {conn.get_extra_info('peername')}")

            def connection_lost(self, exc):
                if self._conn is not None:
                    server_self._active_connections.discard(self._conn)
                if exc:
                    logger.debug(f"[ECHO SSH] Connexion perdue : {exc}")

            def password_auth_supported(self) -> bool:
                return True

            def validate_password(self, username: str, password: str) -> bool:
                """Accepte uniquement echo-auth + mot de passe jetable."""
                ok = (username == tunnel_user and password == temp_password)
                if not ok:
                    logger.warning(f"[ECHO SSH] Auth échouée pour user='{username}'")
                return ok

            def session_requested(self):
                """Accepte l'ouverture d'une session terminale passive."""
                return _EchoServerSession()

            def connection_requested(
                self, dest_host: str, dest_port: int, orig_host: str, orig_port: int
            ):
                """
                Autorise uniquement le port forward vers localhost:callback_port.
                Toute autre destination est refusée.
                """
                if dest_host in ("localhost", "127.0.0.1") and dest_port == callback_port:
                    logger.debug(f"[ECHO SSH] Port forward autorisé → {dest_host}:{dest_port}")
                    return True
                logger.warning(
                    f"[ECHO SSH] Port forward refusé → {dest_host}:{dest_port} "
                    f"(seul localhost:{callback_port} est autorisé)"
                )
                return False

        return _EchoSSHServer

    async def _auto_stop(self) -> None:
        """Arrête automatiquement le serveur après timeout secondes."""
        await asyncio.sleep(self._timeout)
        if self.is_active:
            logger.info(f"[ECHO SSH] Timeout {self._timeout}s — arrêt automatique.")
            await self.stop(message="Authentification OAuth2 : Délai dépassé")
