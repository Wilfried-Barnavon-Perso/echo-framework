# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER SERVER (ENTRYPOINT)
VERSION : 5.226.18.0
AUTEUR : Wilfried BARNAVON
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 5.137: Rapport Nocturne Complet Sauvegarde/Externalisation & Compteur session.db
from core.config import app, HAS_PSUTIL, HAS_SCHEDULER, psutil, backup_scheduler
import core.security  # noqa: F401
import routes.routes  # noqa: F401
from services.maintenance_backup import update_system_schedules

if __name__ == '__main__':
    if HAS_PSUTIL:
        psutil.cpu_percent(interval=None)
    if HAS_SCHEDULER and backup_scheduler is not None:
        if not backup_scheduler.running:
            backup_scheduler.start()
        update_system_schedules()
    app.run(host='0.0.0.0', port=3001, debug=False, threaded=True)
