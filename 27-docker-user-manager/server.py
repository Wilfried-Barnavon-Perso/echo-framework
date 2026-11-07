# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO USER MANAGER SERVER (ENTRYPOINT)
VERSION : 5.236.5.1
AUTEUR : Wilfried BARNAVON
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.236.5.1: Création initiale du point d'entrée User Manager (Port 3002).

from core.config import app
import core.security  # noqa: F401
import routes.routes  # noqa: F401

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3002, debug=False, threaded=True)
