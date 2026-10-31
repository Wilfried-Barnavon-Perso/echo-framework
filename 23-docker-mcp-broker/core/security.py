# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO - SECURITY
VERSION : 5.231.37.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.37.1: Ajout de chat_id_var (ContextVar) pour le routage multi-chat des téléchargements MCP.
# 5.231.20.1: Ajout de credentials_var (ContextVar) pour l'isolation multi-user sur les modules MCP natifs.
# 5.226.18.0: Alignement sur le versioning global ECHO.
import contextvars

# Variables de contexte globales pour stocker l'ID utilisateur, le chat_id et les credentials de la requête courante
user_id_var = contextvars.ContextVar("user_id", default=None)
chat_id_var = contextvars.ContextVar("chat_id", default=None)
credentials_var = contextvars.ContextVar("credentials", default={})
