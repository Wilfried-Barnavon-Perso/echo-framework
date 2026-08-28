"""
================================================================================
MODULE : ECHO MCP BROKER
VERSION : 1.9 (HTTP Status Code Forwarding)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-08-28
================================================================================
"""
from starlette.requests import Request
from modules.m5_proxy_mcp import proxy_mcp_request
from modules.m2_jobs_omnisearch import setup_jobs_omnisearch_mcp
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
    json_response=True,
    stateless_http=True,
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
    },
    "remote_mcp": {
        "name": "Serveurs MCP Distants (HTTP/SSE)",
        "fields": [
            {"id": "url", "label": "URL du Serveur", "type": "text", "help": "ex: https://locataire-averti.com/mcp"},
            {"id": "headers", "label": "En-têtes (JSON)", "type": "text", "help": "ex: {\"Authorization\": \"Bearer XXX\"}"},
            {"id": "description", "label": "Rôle", "type": "text", "help": "Sert au LLM pour savoir quand l'utiliser."}
        ]
    },
    "stdio_mcp": {
        "name": "Serveurs MCP Locaux (Stdio)",
        "fields": [
            {"id": "command", "label": "Commande", "type": "text", "help": "ex: npx ou uvx"},
            {"id": "args", "label": "Arguments (JSON)", "type": "text", "help": "ex: [\"-y\", \"@modelcontextprotocol/server-postgres\"]"},
            {"id": "description", "label": "Rôle", "type": "text", "help": "Sert au LLM pour savoir quand l'utiliser."}
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

# Nouveau routeur pour proxy_mcp


async def proxy_mcp_route(request: Request):
    try:
        payload = await request.json()
        result = await proxy_mcp_request(payload)
        return JSONResponse(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        
        status_code = 500
        message = str(e)
        payload = {"status": "error", "source": "broker_internal", "message": message}
        
        # Interception dynamique de l'erreur httpx/httpx2 (HTTPStatusError)
        if type(e).__name__ == 'HTTPStatusError':
            status_code = 502  # Bad Gateway
            remote_status = getattr(e.response, "status_code", "Inconnu") if hasattr(e, "response") else "Inconnu"
            remote_url = getattr(e.request, "url", "Inconnue") if hasattr(e, "request") else "Inconnue"
            message = f"La cible distante ({remote_url}) a rejeté la requête avec le code {remote_status}."
            payload = {
                "status": "error", 
                "source": "remote_server", 
                "http_code": remote_status,
                "message": message
            }
            
        return JSONResponse(payload, status_code=status_code)

app.add_route("/proxy_mcp", proxy_mcp_route, methods=["POST"])

if __name__ == "__main__":
    # Démarrage du serveur sur le port 8000
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
