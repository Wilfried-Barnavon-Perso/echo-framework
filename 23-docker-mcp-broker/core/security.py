# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO - SECURITY
VERSION : 5.226.18.0
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.226.18.0: Alignement sur le versioning global ECHO.
import contextvars

# Variable de contexte globale pour stocker l'ID utilisateur de la requête asynchrone courante
user_id_var = contextvars.ContextVar("user_id", default=None)
