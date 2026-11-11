"""
title: ECHO V6 Native FastAPI Routers Package
author: Wilfried BARNAVON
version: 6.3.0.1
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.3.0.1: Intégration des routeurs wm, cognition, codex et vault dans le package souverain ECHO.
# 6.2.0.1: Initialisation du sous-package FastAPI des routeurs souverains ECHO.

from open_webui.echo.routers import (
    codex,
    cognition,
    meta_artifacts,
    skills,
    vault,
    wm,
)

__all__ = [
    "codex",
    "cognition",
    "meta_artifacts",
    "skills",
    "vault",
    "wm",
]
