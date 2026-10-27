# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO - SECURITY
VERSION : 5.231.20.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.20.1: Ajout de credentials_var (ContextVar) pour l'isolation multi-user des credentials sur les modules MCP natifs.
# 5.226.18.0: Alignement sur le versioning global ECHO.
import contextvars

# Variables de contexte globales pour stocker l'ID utilisateur et les credentials de la requête asynchrone courante
user_id_var = contextvars.ContextVar("user_id", default=None)
credentials_var = contextvars.ContextVar("credentials", default={})
