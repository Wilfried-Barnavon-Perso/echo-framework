"""
================================================================================
MODULE : ECHO MCP BROKER
VERSION : 1.6 (Migration MCP V2)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-08-09
================================================================================
"""
from core.security import user_id_var
from starlette.responses import PlainTextResponse, JSONResponse
from modules.m4_academic import register_academic_tools
from modules.m3_corporate import register_corporate_tools
from mcp.server import MCPServer
import uvicorn

from mcp.server.transport_security import TransportSecuritySettings

# Initialisation du serveur MCP avec protection DNS Rebinding désactivée
# (Docker interne)
mcp = MCPServer("ECHO_MCP_Broker")

# Importation des modules pour enregistrer les outils

from modules.m2_jobs_omnisearch import setup_jobs_omnisearch_mcp
setup_jobs_omnisearch_mcp(mcp)
register_corporate_tools(mcp)
register_academic_tools(mcp)


@mcp.tool()
async def ping() -> str:
    """Outil de test basique pour vérifier la connectivité du Broker via MCP."""
    return "pong"

# FastMCP et Open WebUI communiquent via StreamableHTTP (et non SSE standard)
# Nous utiliserons Uvicorn pour exposer l'application ASGI générée par mcp.
app = mcp.streamable_http_app(
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))

# Route HTTP pour le Docker Healthcheck


async def healthcheck_ping(request):
    return PlainTextResponse("pong")
app.add_route("/ping", healthcheck_ping)

# Définition dynamique des schémas d'authentification des services MCP
SERVICE_SCHEMAS = {
    "corporate": {
        "name": "Corporate (Sirene/Pappers)",
        "fields": [
            {"id": "sirene_key", "label": "Clé API Sirene", "type": "password", "help": "Token Bearer obtenu sur l'INSEE."},
            {"id": "pappers_key", "label": "Clé API Pappers", "type": "password",
                "help": "Clé API obtenue sur pappers.fr (Espace Développeurs)."}
        ]
    },
    "academic": {
        "name": "Academic",
        "fields": [
            {"id": "api_key", "label": "Clé API (Optionnelle)", "type": "password"}
        ]
    }
}


async def get_schemas(request):
    return JSONResponse(SERVICE_SCHEMAS)
app.add_route("/schemas", get_schemas)


class WebUIUserMiddleware:
    """
    Middleware pur ASGI qui intercepte chaque requête entrante (y compris POST /messages de l'outil MCP)
    et extrait l'identité de l'utilisateur envoyée par Open WebUI pour l'injecter dans le contexte global.
    L'utilisation d'ASGI pur (au lieu de BaseHTTPMiddleware) est obligatoire pour ne pas bloquer les flux SSE.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            user_id = headers.get(b"x-openwebui-user-id", b"").decode("utf-8")

            if not user_id:
                user_id = headers.get(b"x-echo-user-id", b"").decode("utf-8")

            if user_id:
                user_id_var.set(user_id)

        await self.app(scope, receive, send)


app.add_middleware(WebUIUserMiddleware)

if __name__ == "__main__":
    # Démarrage du serveur sur le port 8000
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
