from functools import wraps
from mcp.server.context import Context
from .database import get_credentials
import contextvars

# Variable de contexte globale pour stocker l'ID utilisateur de la requête asynchrone courante
user_id_var = contextvars.ContextVar("user_id", default=None)


def require_service_access(service: str):
    """
    Décorateur pour les outils MCP qui requièrent un accès vérifié au service.
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

                if ctx and hasattr(ctx, "headers"):
                    user_id = ctx.headers.get("x-openwebui-user-id")
                    if not user_id:
                        user_id = ctx.headers.get("x-echo-user-id")

            if not user_id:
                raise RuntimeError("User ID not found in context. Authentication via Open WebUI headers is required.")

            # 2. Vérifier les droits en base (Accès Unique)
            vault_data = await get_credentials(user_id, service)
            if not vault_data:
                raise PermissionError(f"No credentials found for service '{service}'.")

            # Execution autorisée
            return await func(*args, **kwargs)

        return wrapper
    return decorator
