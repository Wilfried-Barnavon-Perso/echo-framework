import inspect
from functools import wraps
from mcp.server.fastmcp import Context
from .database import get_credentials
import contextvars

# Variable de contexte globale pour stocker l'ID utilisateur de la requête asynchrone courante
user_id_var = contextvars.ContextVar("user_id", default=None)


def require_rw_access(service: str):
    """
    Décorateur pour les outils MCP qui requièrent un accès en écriture (RW).
    FastMCP fournira le `Context` (ctx) automatiquement.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 1. Récupération de l'identité utilisateur injectée par le middleware
            user_id = user_id_var.get()

            if not user_id:
                # Fallback : Extraire le context FastMCP si existant
                ctx = kwargs.get('ctx')
                if not isinstance(ctx, Context):
                    for arg in args:
                        if isinstance(arg, Context):
                            ctx = arg
                            break

                if ctx and hasattr(ctx, "request_context") and ctx.request_context:
                    request = ctx.request_context
                    if hasattr(request, "headers"):
                        user_id = request.headers.get("x-openwebui-user-id")
                        if not user_id:
                            user_id = request.headers.get("x-echo-user-id")

            if not user_id:
                raise RuntimeError("User ID not found in context. Authentication via Open WebUI headers is required.")

            # 2. Vérifier les droits en base (Accès Unique)
            vault_data = await get_credentials(user_id, service)
            if not vault_data:
                raise PermissionError(f"No credentials found for service '{service}'.")

            if vault_data["access_level"] != "RW":
                raise PermissionError(
                    f"Permission Denied: Service '{service}' is configured in Read-Only (RO) mode. "
                    "Write operations are blocked."
                )

            # Execution autorisée
            return await func(*args, **kwargs)

        return wrapper
    return decorator
