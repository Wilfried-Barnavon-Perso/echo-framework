import aiosqlite
import os

# Dans l'architecture ECHO, identity.db est partagé via le volume /app/backend/data
# Toutefois, identity.db est généralement dans /app/backend/data/users/<user_id>/identity.db
# Etant donné que le Broker est un conteneur indépendant, il doit monter ce volume.
ECHO_DATA_DIR = os.environ.get("ECHO_DATA_DIR", "/app/backend/data")


async def get_db_path(user_id: str) -> str:
    # Reproduit la logique de EchoStateManager pour trouver la BDD de l'utilisateur
    safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
    db_path = os.path.join(ECHO_DATA_DIR, "users", safe_uid, "identity.db")

    # Fallback si l'admin utilise une structure différente
    if not os.path.exists(db_path):
        db_path = os.path.join(ECHO_DATA_DIR, safe_uid, "identity.db")

    return db_path


async def get_credentials(user_id: str, service: str) -> dict:
    """Récupère les identifiants depuis le Vault (identity.db) en accès unique"""
    db_path = await get_db_path(user_id)
    if not os.path.exists(db_path):
        return None

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT credentials FROM identity_vault WHERE user_id = ? AND service = ? LIMIT 1",
            (user_id, service)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "credentials": row[0]
                }
    return None
