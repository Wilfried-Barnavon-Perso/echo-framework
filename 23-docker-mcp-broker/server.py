"""
================================================================================
MODULE : ECHO MCP BROKER
VERSION : 5.228.9.1
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-10-21
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.228.9.1: Directives France Travail (OAuth2 francetravail.io) et champs WTTJ dans jobs_omnisearch.
# 5.228.0.1: Directive PAS exhaustive pour google_workspace (anti-localhost:8001, mode In production).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.14: Interception ciblée AuthRequiredError
import logging
import uvicorn
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse
try:
    from mcp.server import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from core.security import user_id_var
from modules.m5_proxy_mcp import proxy_mcp_request, AuthRequiredError
from modules.m2_jobs_omnisearch import setup_jobs_omnisearch_mcp
from modules.m3_corporate import register_corporate_tools
from modules.m4_academic import register_academic_tools

logger = logging.getLogger(__name__)

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
        "category": "mcp_native",
        "name": "Corporate (Sirene/Pappers)",
        "fields": [
            {"id": "sirene_key", "label": "Clé API Sirene", "type": "password", "help": "Token Bearer obtenu sur l'INSEE."},
            {"id": "pappers_key", "label": "Clé API Pappers", "type": "password",
                "help": "Clé API obtenue sur pappers.fr (Espace Développeurs)."}
        ]
    },
    "jobs_omnisearch": {
        "category": "mcp_native",
        "name": "Emplois & Carrières (Omnisearch)",
        "user_required_instructions": (
            "DIRECTIVES IMPÉRATIVES DE CONFIGURATION FRANCE TRAVAIL (FRANCETRAVAIL.IO) :\n"
            "1. ACCÈS AU PORTAIL DÉVELOPPEUR : Se rendre sur https://francetravail.io/ et se connecter "
            "(ou créer un compte développeur gratuit).\n"
            "2. CRÉATION D'UNE APPLICATION : Accéder à l'onglet 'Mes applications' puis cliquer sur 'Ajouter une application'.\n"
            "3. SOUSCRIPTION AU PACKAGE : Dans le catalogue d'APIs, associer le package 'Offres d'emploi v2' "
            "(accès public, libre et gratuit).\n"
            "4. RÉCUPÉRATION DES IDENTIFIANTS : Dans la fiche de l'application créée, "
            "copier le 'Client ID' et le 'Client Secret'.\n"
            "5. CONSIGNATION DANS L'IDENTITY VAULT :\n"
            "   Enregistrer dans l'Identity Vault pour le service 'jobs_omnisearch' les deux clés exactes :\n"
            "   - 'FRANCE_TRAVAIL_CLIENT_ID' : Votre Client ID francetravail.io\n"
            "   - 'FRANCE_TRAVAIL_CLIENT_SECRET' : Votre Client Secret francetravail.io\n"
            "   (Alternative : définir ces variables dans l'environnement Docker ou le fichier .env d'ECHO).\n"
            "   Sans ces clés, la recherche reste pleinement opérationnelle sur LinkedIn, APEC et WTTJ."
        ),
        "fields": [
            {
                "id": "FRANCE_TRAVAIL_CLIENT_ID",
                "label": "Client ID France Travail",
                "type": "text",
                "required": False,
                "help": "Obtenu sur francetravail.io"
            },
            {
                "id": "FRANCE_TRAVAIL_CLIENT_SECRET",
                "label": "Client Secret France Travail",
                "type": "password",
                "required": False,
                "help": "Obtenu sur francetravail.io"
            },
            {
                "id": "WTTJ_ALGOLIA_API_KEY",
                "label": "Clé Algolia WTTJ (Optionnelle)",
                "type": "password",
                "required": False,
                "help": "Clé publique WTTJ (si rotation)"
            }
        ]
    },
    "academic": {
        "category": "mcp_native",
        "name": "Academic",
        "fields": [
            {"id": "api_key", "label": "Clé API (Optionnelle)", "type": "password"}
        ]
    },
    "google_workspace": {
        "category": "mcp_resident",
        "name": "Google Workspace MCP (Résident)",
        "command": "workspace-mcp",
        "user_required_instructions": (
            "DIRECTIVES IMPÉRATIVES D'AUTHENTIFICATION GOOGLE WORKSPACE :\n"
            "1. PROSCRIPTION LOCALE : Le Modèle a l'INTERDICTION STRICTE de proposer ou d'afficher des URLs de redirection locales "
            "(ex: http://localhost:8001/oauth2callback). L'infrastructure opère dans un conteneur headless isolé inaccessible depuis le navigateur de l'Utilisateur.\n"
            "2. PRÉREQUIS GOOGLE CLOUD CONSOLE (SI PREMIÈRE CONFIGURATION) :\n"
            "   a. L'Utilisateur DOIT créer un projet sur Google Cloud Console et activer les APIs requises "
            "(Calendar, Gmail, Drive, Contacts, Sheets, Docs, Slides, Tasks).\n"
            "   b. Dans 'Identifiants', créer un ID client OAuth de type 'Application Web' et déclarer impérativement l'URI de redirection autorisée : "
            "https://developers.google.com/oauthplayground\n"
            "3. PÉRENNITÉ DU JETON (ANTI-EXPIRATION 7 JOURS) :\n"
            "   Dans Google Cloud Console > 'Écran de consentement OAuth' (Google Auth Platform > Audience), l'Utilisateur DOIT basculer le statut "
            "de publication de 'Testing' (Test) à 'In production' (En production). Sans cette action, Google invalide déterministement "
            "le Refresh Token tous les 7 jours (erreur invalid_grant).\n"
            "4. GÉNÉRATION / RÉGÉNÉRATION DU REFRESH TOKEN (OAuth Playground) :\n"
            "   a. L'Utilisateur DOIT accéder à https://developers.google.com/oauthplayground\n"
            "   b. Ouvrir les paramètres (icône engrenage en haut à droite), cocher 'Use your own OAuth credentials' et renseigner le 'OAuth Client ID' et le 'OAuth Client secret'.\n"
            "   c. Dans 'Step 1 - Select & authorize APIs' > champ 'Input your own scopes', coller la liste exhaustive suivante :\n"
            "      openid https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/userinfo.email "
            "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/contacts https://www.googleapis.com/auth/spreadsheets "
            "https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/presentations https://www.googleapis.com/auth/tasks\n"
            "   d. Cliquer sur 'Authorize APIs' et valider l'accès avec le compte Google cible.\n"
            "   e. Dans 'Step 2 - Exchange authorization code for tokens', cliquer sur 'Exchange authorization code for tokens' et copier la valeur de 'Refresh token'.\n"
            "5. CONSIGNATION DANS L'IDENTITY VAULT :\n"
            "   Les 4 clés exactes suivantes DOIVENT être enregistrées dans l'Identity Vault sous le service 'google_workspace' "
            "(ou mettre à jour 'GOOGLE_OAUTH_REFRESH_TOKEN' s'il s'agit d'un simple renouvellement) : "
            "'USER_GOOGLE_EMAIL', 'GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_SECRET', 'GOOGLE_OAUTH_REFRESH_TOKEN'."
        ),
        "fields": [
            {"id": "USER_GOOGLE_EMAIL", "label": "Email Google", "type": "text", "required": True, "help": "Email du compte (ex: john.doe@gmail.com)"},
            {"id": "GOOGLE_OAUTH_CLIENT_ID", "label": "Client ID", "type": "text", "required": True, "help": "ID Client OAuth2 Google"},
            {"id": "GOOGLE_OAUTH_CLIENT_SECRET", "label": "Client Secret", "type": "password", "required": True, "help": "Secret Client OAuth2 Google"},
            {"id": "GOOGLE_OAUTH_REFRESH_TOKEN", "label": "Refresh Token", "type": "password", "required": True, "help": "Généré via OAuth Playground"}
        ]
    },
    "microsoft_365": {
        "category": "mcp_resident",
        "name": "Microsoft 365 MCP (Résident)",
        "command": "microsoft-mcp",
        "user_required_instructions": (
            "Le Modèle DOIT guider l'Utilisateur pour configurer Microsoft 365 si les identifiants sont manquants ou invalides : "
            "1. Demander à l'Utilisateur de créer une application sur le portail Azure (Entra ID) avec la plateforme 'Application mobile et de bureau' "
            "(Redirect URI natif) et d'ajouter les permissions Microsoft Graph déléguées requises : "
            "Mail.ReadWrite, Mail.Send, Calendars.ReadWrite, User.Read, Files.ReadWrite.All, offline_access. "
            "2. L'Utilisateur doit enregistrer dans l'Identity Vault pour le service 'microsoft_365' les deux clés exactes suivantes : "
            "'MICROSOFT_MCP_CLIENT_ID' (Client ID / Application ID Azure) et 'MICROSOFT_MCP_TENANT_ID' "
            "(Tenant ID, ex: 'consumers' pour les comptes personnels @outlook/@hotmail, ou l'ID d'annuaire de l'organisation). "
            "3. Une fois les clés renseignées dans le Vault, le Modèle DOIT appeler la fonction 'authenticate_account' du MCP Microsoft. "
            "4. Le Modèle DOIT restituer à l'Utilisateur l'URL de vérification ainsi que le 'User Code' fournis par le MCP, et inviter l'Utilisateur à valider la connexion. "
            "5. Après confirmation de l'Utilisateur, le Modèle DOIT appeler la fonction 'complete_authentication' avec le 'flow_cache' pour clore l'authentification MSAL."
        ),
        "fields": [
            {"id": "MICROSOFT_MCP_CLIENT_ID", "label": "App ID Azure", "type": "text", "required": True, "help": "ID de l'application enregistrée sur Azure"},
            {"id": "MICROSOFT_MCP_TENANT_ID", "label": "Tenant ID", "type": "text", "required": True, "help": "ID du Tenant (consumers, common, etc.)"}
        ]
    },
    "remote_mcp": {
        "category": "mcp_remote",
        "name": "Serveurs MCP Distants (HTTP/SSE)",
        "fields": [
            {"id": "url", "label": "URL du Serveur", "type": "text", "help": "ex: https://locataire-averti.com/mcp/sse"},
            {"id": "transport", "label": "Type de Transport", "type": "text", "help": "Saisir obligatoirement 'sse' ou 'streamable_http'."},
            {"id": "headers", "label": "En-têtes (JSON)", "type": "text", "help": "ex: {\"Authorization\": \"Bearer XXX\"}"},
            {"id": "description", "label": "Rôle", "type": "text", "help": "Sert au LLM pour savoir quand l'utiliser."}
        ]
    },
    "stdio_mcp": {
        "category": "mcp_ephemeral",
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
    payload = {}
    try:
        payload = await request.json()
        result = await proxy_mcp_request(payload)
        return JSONResponse(result)
    except AuthRequiredError as ae:
        logger.warning(f"Erreur de prérequis Proxy MCP : {ae}")
        service_name = payload.get("service", "")
        schema_def = SERVICE_SCHEMAS.get(service_name, {})
        instructions = schema_def.get("user_required_instructions", "")
        return JSONResponse({
            "status": "error",
            "source": "auth_required",
            "service": service_name,
            "message": str(ae),
            "user_required_instructions": instructions
        }, status_code=400)
    except Exception as e:
        logger.exception("Erreur Proxy MCP globale :")

        status_code = 500
        message = repr(e) if not str(e).strip() else str(e)
        error_payload = {"status": "error", "source": "broker_internal", "message": message}

        # Interception dynamique de l'erreur httpx/httpx2 (HTTPStatusError)
        if type(e).__name__ == 'HTTPStatusError':
            status_code = 502  # Bad Gateway
            remote_status = getattr(e.response, "status_code", "Inconnu") if hasattr(e, "response") else "Inconnu"
            remote_url = getattr(e.request, "url", "Inconnue") if hasattr(e, "request") else "Inconnue"
            message = f"La cible distante ({remote_url}) a rejeté la requête avec le code {remote_status}."
            error_payload = {
                "status": "error",
                "source": "remote_server",
                "http_code": remote_status,
                "message": message
            }

        return JSONResponse(error_payload, status_code=status_code)

app.add_route("/proxy_mcp", proxy_mcp_route, methods=["POST"])

if __name__ == "__main__":
    # Démarrage du serveur sur le port 8000
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
