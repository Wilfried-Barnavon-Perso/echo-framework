import contextvars

# Variable de contexte globale pour stocker l'ID utilisateur de la requête asynchrone courante
user_id_var = contextvars.ContextVar("user_id", default=None)
