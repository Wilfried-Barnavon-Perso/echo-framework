# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - UI TEMPLATES
VERSION : 5.137
================================================================================
"""

__all__ = [
    'HTML_LOGIN',
    'HTML_DASHBOARD',
]

HTML_LOGIN = """
<!doctype html><html lang="fr" data-bs-theme="dark"><head><meta charset="utf-8"><title>Login - ECHO Admin</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-black d-flex align-items-center justify-content-center vh-100"><div class="card p-5 border-secondary shadow-lg" style="width:400px;"><h2 class="text-center text-primary mb-4">ECHO Admin</h2><form method="POST"><input type="text" name="username" class="form-control mb-3" placeholder="Admin" required autofocus><input type="password" name="password" class="form-control mb-4" placeholder="Password" required><button class="btn btn-primary w-100">Entrer</button></form></div></body></html>"""

HTML_DASHBOARD = """
<!doctype html><html lang="fr" data-bs-theme="dark"><head><meta charset="utf-8"><title>Console - ECHO Admin</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css"><style>.card{background-color:#161b22;border-color:#30363d;overflow:hidden;display:flex;flex-direction:column}.card>.card-body{overflow-y:auto;flex:1 1 auto}.navbar{background-color:#161b22;border-bottom:1px solid #30363d}.table{--bs-table-bg:transparent;--bs-table-border-color:#30363d;color:#c9d1d9}.x-small{font-size:0.7rem;color:#888}#v-pills-tab .nav-link{color:#c9d1d9; border-radius: 0.375rem; transition: background-color 0.2s, color 0.2s;}#v-pills-tab .nav-link:hover{background-color:rgba(255,255,255,0.05);}#v-pills-tab .nav-link.active{background-color:#30363d;color:#fff;font-weight:bold;}</style></head>
<body style="background-color:#0d1117;">
    <div id="loader" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:9999;display:none;flex-direction:column;justify-content:center;align-items:center;"><div class="spinner-border text-primary mb-3"></div><h4 id="loader-msg">Action en cours...</h4></div>
    <nav class="navbar px-4 py-2 mb-4"><span class="navbar-brand text-primary fw-bold" data-bs-toggle="tooltip" title="ECHO Infrastructure Manager"><i class="bi bi-tree-fill"></i> ECHO CONSOLE {{ version }}</span><div class="d-flex align-items-center gap-3"><span class="badge bg-dark border border-secondary" id="clock">--:--:--</span><span class="text-muted small">{{ user }}</span><a href="/logout" class="btn btn-sm btn-outline-danger">Quitter</a></div></nav>
    <div class="container-fluid px-4">
        {% with msgs = get_flashed_messages(with_categories=true) %}{% for c,m in msgs %}<div class="alert alert-{{c}} alert-dismissible fade show border-0 mb-4">{{m|safe}}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}{% endwith %}
        
        <div class="row g-3 mb-4">
            <div class="col-md-2 col-sm-4"><div class="card h-100 p-3"><div class="d-flex justify-content-between align-items-center"><span class="fw-bold small text-muted">CPU</span><span class="fs-4 fw-bold text-primary"><span id="cpu">--</span>%</span></div><div class="d-flex justify-content-between align-items-center mt-1"><small class="x-small" id="cpu-details">...</small></div><div class="progress mt-2" style="height:4px;"><div id="cpu-bar" class="progress-bar bg-primary" style="width:0%;"></div></div></div></div>
            <div class="col-md-2 col-sm-4"><div class="card h-100 p-3"><div class="d-flex justify-content-between align-items-center"><span class="fw-bold small text-muted">RAM</span><span class="fs-4 fw-bold text-info"><span id="ram">--</span>%</span></div><div class="d-flex justify-content-between align-items-center mt-1"><small class="x-small" id="ram-text">--/--</small></div><div class="progress mt-2" style="height:4px;"><div id="ram-bar" class="progress-bar bg-info" style="width:0%;"></div></div></div></div>
            <div class="col-md-2 col-sm-4"><div class="card h-100 p-3"><div class="d-flex justify-content-between align-items-center"><span class="fw-bold small text-muted">Disque</span><span class="fs-4 fw-bold" id="disk-pct-wrap"><span id="disk-pct">--</span>%</span></div><div class="d-flex justify-content-between align-items-center mt-1"><small class="x-small" id="disk-text">--/--</small></div><div class="progress mt-2" style="height:4px;"><div id="disk-bar" class="progress-bar" style="width:0%;"></div></div></div></div>
            <div class="col-md-3 col-sm-6"><div class="card h-100 p-3 d-flex flex-column justify-content-center text-center"><span class="fw-bold small text-muted mb-1" data-bs-toggle="tooltip" title="OWUI DB / Fichiers .db physiques">Sessions Actives / Totales</span><span class="text-primary fs-3 fw-bold">{{ storage_stats.owui_sessions }} / {{ storage_stats.real_sessions }}</span></div></div>
            <div class="col-md-3 col-sm-6"><div class="card h-100 p-3 d-flex flex-column justify-content-center text-center"><span class="fw-bold small text-muted mb-1">Volume Espace Personnel</span><span class="text-success fs-4 fw-bold">{{ storage_stats.vault.size_fmt }}</span></div></div>
        </div>

        <div id="task-progress-card" class="card border-info mb-4 shadow-sm" style="display:none;"><div class="card-body py-2 px-3"><div class="d-flex justify-content-between align-items-center mb-1"><div class="d-flex align-items-center gap-2"><span class="spinner-border spinner-border-sm text-info" role="status"></span><span id="task-progress-status" class="fw-bold small text-light">Opération en cours...</span></div><div class="d-flex align-items-center gap-2"><span id="task-progress-elapsed" class="badge bg-dark border border-secondary text-muted font-monospace">0s</span><span id="task-progress-pct" class="badge bg-info text-dark fw-bold font-monospace">0%</span></div></div><div class="progress" style="height:8px;background-color:#21262d;"><div id="task-progress-bar" class="progress-bar progress-bar-striped progress-bar-animated bg-info" role="progressbar" style="width:0%;"></div></div></div></div>

        <div class="row">
            <div class="col-lg-2 col-md-3 mb-4">
                <div class="nav flex-column nav-pills gap-1" id="v-pills-tab" role="tablist" aria-orientation="vertical">
                    <button class="nav-link active text-start py-2" data-bs-toggle="pill" data-bs-target="#v-pills-identity" type="button" role="tab" onclick="refreshAuthUsers(); refreshUsers(); refreshAuthSessions();"><i class="bi bi-person-badge"></i> Identités & Sessions</button>
                    <button class="nav-link text-start py-2 text-info" data-bs-toggle="pill" data-bs-target="#v-pills-maint" type="button" role="tab" onclick="refreshMaintHistory()"><i class="bi bi-scissors"></i> Système & Maintenance</button>
                    <button class="nav-link text-start py-2 text-success" data-bs-toggle="pill" data-bs-target="#v-pills-backup" type="button" role="tab" onclick="refreshBackups(); refreshMaintHistory();"><i class="bi bi-hdd-network"></i> Sauvegardes</button>
                    <button class="nav-link text-start py-2 text-warning" data-bs-toggle="pill" data-bs-target="#v-pills-security" type="button" role="tab"><i class="bi bi-shield-lock"></i> Sécurité & Accès</button>
                    <button class="nav-link text-start py-2 text-secondary" data-bs-toggle="pill" data-bs-target="#v-pills-docker" type="button" role="tab" onclick="refreshContainers()"><i class="bi bi-box"></i> Conteneurs Docker</button>
                </div>
            </div>
            
            <div class="col-lg-10 col-md-9">
                <div class="tab-content" id="v-pills-tabContent">
                    
                    <div class="tab-pane fade show active" id="v-pills-identity" role="tabpanel">
                        <div class="card border-primary mb-3"><div class="card-header text-primary d-flex justify-content-between align-items-center"><span><i class="bi bi-person-badge"></i> Identités & Sessions (SSO / WebUI)</span><div class="btn-group"><button class="btn btn-sm btn-outline-primary" onclick="refreshAuthUsers(); refreshUsers(); refreshAuthSessions();"><i class="bi bi-arrow-repeat"></i></button><button class="btn btn-sm btn-outline-warning" data-bs-toggle="modal" data-bs-target="#authSettingsModal" title="Paramètres de Sécurité"><i class="bi bi-gear"></i></button></div></div><div class="card-body p-2 d-flex flex-column"><ul class="nav nav-pills nav-fill mb-2" id="identityPills" role="tablist"><li class="nav-item" role="presentation"><button class="nav-link active py-1 x-small text-info" data-bs-toggle="pill" data-bs-target="#webuiActivityPane" type="button" role="tab" onclick="refreshUsers()">💬 Activité Open WebUI</button></li><li class="nav-item" role="presentation"><button class="nav-link py-1 x-small" data-bs-toggle="pill" data-bs-target="#ssoAccountsPane" type="button" role="tab">👤 Comptes SSO (ECHO Auth)</button></li><li class="nav-item" role="presentation"><button class="nav-link py-1 x-small text-warning" data-bs-toggle="pill" data-bs-target="#ssoSessionsPane" type="button" role="tab" onclick="refreshAuthSessions()">🛡️ Connexions SSO Actives</button></li></ul><div class="tab-content flex-grow-1"><div class="tab-pane fade show active" id="webuiActivityPane" role="tabpanel"><table class="table table-sm table-hover mb-0"><thead><tr><th class="ps-3">Nom</th><th>Email</th><th class="text-center">Sessions</th><th class="text-end pe-3">Actions</th></tr></thead><tbody id="user-list"></tbody></table></div><div class="tab-pane fade" id="ssoAccountsPane" role="tabpanel"><div class="d-flex justify-content-between mb-2"><form action="/action/auth/create" method="post" class="d-flex gap-2" onsubmit="showLoader('Création...')"><input type="text" name="name" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Nom (Optionnel)"><input type="email" name="email" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Nouvel Email Utilisateur" required><button type="submit" class="btn btn-sm btn-primary">Créer</button></form><div class="d-flex"><form action="/action/auth/import" method="post" onsubmit="showLoader('Importation...')"><button type="submit" class="btn btn-sm btn-outline-info" data-bs-toggle="tooltip" title="Importer les emails depuis Open WebUI"><i class="bi bi-box-arrow-in-down"></i> Importer DB</button></form><form action="/action/auth/import_admin" method="post" onsubmit="showLoader('Importation Admin...')"><button type="submit" class="btn btn-sm btn-warning ms-1" data-bs-toggle="tooltip" title="Importer le compte système Admin"><i class="bi bi-person-badge-fill"></i> Import Admin</button></form></div></div><table class="table table-sm table-dark table-hover mb-0"><thead><tr><th>Email</th><th>Statut</th><th class="text-end">Actions</th></tr></thead><tbody id="auth-users-list"></tbody></table></div><div class="tab-pane fade" id="ssoSessionsPane" role="tabpanel"><div class="d-flex justify-content-between align-items-center mb-2 mt-1"><h6 class="mb-0 text-warning"><i class="bi bi-shield-check"></i> Connexions Actives</h6><button class="btn btn-sm btn-outline-danger" onclick="revokeAllAuthSessions()" title="Purger toutes les sessions actives"><i class="bi bi-shield-x"></i> Purge Globale</button></div><table class="table table-sm table-dark table-hover mb-0"><thead><tr><th>Compte / ID</th><th>Appareil & Navigateur</th><th>IP & Lieu</th><th>Dates</th><th class="text-end">Actions</th></tr></thead><tbody id="auth-sessions-list"><tr><td colspan="5" class="text-center text-muted">Chargement...</td></tr></tbody></table></div></div></div></div>
                    </div>

                    <div class="tab-pane fade" id="v-pills-maint" role="tabpanel">
                        <div class="card border-info mb-3"><div class="card-header text-info"><i class="bi bi-scissors"></i> Élagage & Cycle de Vie (Jours)</div><div class="card-body small">
                            <form action="/settings/maintenance" method="post" class="mb-3"><div class="row g-2 mb-2"><div class="col-12"><label class="x-small text-muted">Durée de conservation de la mémoire (TTL par niveau) :</label></div><div class="col text-center"><label class="x-small text-secondary mb-1">Trivial</label><input type="number" name="ttl_lvl1" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl1}}" title="Lv1 (Trivial)"></div><div class="col text-center"><label class="x-small text-secondary mb-1">Mineur</label><input type="number" name="ttl_lvl2" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl2}}" title="Lv2"></div><div class="col text-center"><label class="x-small text-secondary mb-1">Utile</label><input type="number" name="ttl_lvl3" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl3}}" title="Lv3"></div><div class="col text-center"><label class="x-small text-secondary mb-1">Majeur</label><input type="number" name="ttl_lvl4" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl4}}" title="Lv4"></div><div class="col text-center"><label class="x-small text-secondary mb-1">Axiome</label><input type="number" name="ttl_lvl5" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl5}}" title="Lv5 (Axiome/Critique)"></div></div><div class="d-flex justify-content-between align-items-end"><label class="x-small fw-bold text-light mt-2">Heure du Cycle de Maintenance (Purge & Consolidation)</label></div><div id="maint-sync-warning" class="alert alert-dark x-small py-1 mb-2 mt-1 border border-secondary text-muted" style="display: none;"><i class="bi bi-info-circle text-info"></i> L'heure est synchronisée avec la sauvegarde automatique.</div><input type="time" name="cleanup_hour" id="cleanup_hour_input" class="form-control form-control-sm mb-2" value="{{maint.cleanup_hour}}"><div class="row g-2 mb-2"><div class="col-12"><label class="x-small text-muted">Consolidation mémoire lvl1 → lvl2 :</label></div><div class="col-6"><label class="x-small text-secondary mb-1">Seuil (nb lvl1)</label><input type="number" name="consol_threshold" class="form-control form-control-sm text-center" value="{{maint.consolidation.trigger_threshold}}" title="Nb de souvenirs Triviaux par user avant consolidation" min="3" max="50"></div><div class="col-6"><label class="x-small text-secondary mb-1">Cluster min</label><input type="number" name="consol_min_cluster" class="form-control form-control-sm text-center" value="{{maint.consolidation.min_cluster_size}}" title="Nb minimum de souvenirs similaires pour fusionner" min="2" max="10"></div><div class="col-12 mt-1"><label class="x-small text-secondary mb-1">Seuil cosinus (0.0-1.0)</label><input type="number" name="consol_similarity" class="form-control form-control-sm text-center" value="{{maint.consolidation.similarity_threshold}}" title="Score cosinus minimal pour regrouper deux souvenirs dans un même cluster (0.75 = très similaires, 0.5 = assez proches)" min="0.4" max="0.99" step="0.05"></div></div><div class="form-check form-switch mb-1"><input class="form-check-input" type="checkbox" name="purge_orphaned_chats" id="sw_purge_chats" {{ 'checked' if maint.purge_orphaned_chats }}><label class="form-check-label x-small" for="sw_purge_chats" data-bs-toggle="tooltip" title="Si activé, supprime les fichiers d'un chat dans l'Espace Personnel ECHO si le chat n'existe plus dans Open WebUI.">Purger les chats orphelins</label></div><div class="form-check form-switch mb-2"><input class="form-check-input" type="checkbox" name="purge_orphaned_users" id="sw_purge_users" {{ 'checked' if maint.purge_orphaned_users }}><label class="form-check-label x-small" for="sw_purge_users" data-bs-toggle="tooltip" title="Si activé, détruit l'Espace Personnel complet (fichiers, bases, mémoires vectorielles) d'un utilisateur supprimé d'Open WebUI.">Purger les utilisateurs orphelins</label></div><div class="d-flex gap-2"><button type="submit" name="submit_action" value="save" class="btn btn-sm btn-info w-100">Programmer le Cycle</button><button type="submit" name="submit_action" value="run" id="btn-run-pruning" class="btn btn-sm btn-outline-info w-100">Lancer le Cycle</button></div></form><hr><p class="m-0 mb-1">Transit (Uploads) : <b>{{ storage_stats.uploads.size_fmt }}</b></p><button class="btn btn-sm btn-outline-secondary w-100" data-bs-toggle="collapse" data-bs-target="#historyLog" onclick="refreshMaintHistory()"><i class="bi bi-journal-text"></i> Afficher les Logs</button><form action="/action/consolidate" method="post" class="mt-2"><button id="btn-consolidate" class="btn btn-outline-warning btn-sm w-100" title="Fusionne les souvenirs Triviaux similaires en souvenirs Mineurs (centroïde vectoriel).">🧬 Consolider Mémoires Lvl1</button></form><form action="/action/docker_prune" method="post" onsubmit="return confirm('Purger les images orphelines, le cache de build Docker et le cache APT système ?') && (showLoader('Purge en cours...'), true)"><button class="btn btn-outline-danger btn-sm w-100 mt-1" title="Libère l'espace des images orphelines (<none>:<none>), du build cache Docker et du cache APT système.">🧹 Purge Cache & Orphelines</button></form><div class="collapse mt-3" id="historyLog"><div class="bg-dark p-2 rounded border border-secondary" style="max-height: 440px; overflow-y: auto;"><h6 class="x-small text-uppercase text-muted border-bottom border-secondary pb-1">Historique 1 an</h6><div id="historyLogList">{% for entry in history %}<div class="mb-2 pb-1 border-bottom border-secondary last-child-border-0"><span class="x-small text-info">{{ entry.timestamp }}</span><br><span style="font-size: 0.75rem;">{{ entry.report | safe }}</span></div>{% endfor %}{% if not history %}<span class="x-small text-muted">Aucun log disponible.</span>{% endif %}</div></div></div>
                        </div></div>
                    </div>

                    <div class="tab-pane fade" id="v-pills-backup" role="tabpanel">
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <div class="card h-100"><div class="card-header d-flex justify-content-between align-items-center"><span><i class="bi bi-hdd-network"></i> Sauvegardes <span class="badge bg-secondary ms-1" data-bs-toggle="tooltip" title="Espace total occupé par les backups locaux">{{ storage_stats.backup_size }}</span></span><div class="btn-group"><button class="btn btn-sm btn-outline-secondary" onclick="refreshBackups(); refreshMaintHistory();"><i class="bi bi-arrow-repeat"></i></button><form action="/action/backup" method="post"><button id="btn-manual-backup" class="btn btn-sm btn-success" title="Lancer une sauvegarde complète (Locale + Externalisation)">+</button></form></div></div><div class="p-0"><table class="table table-sm mb-0"><thead><tr><th class="ps-3">Fichier</th><th>Date</th><th>Taille</th><th class="text-end pe-3">Action</th></tr></thead><tbody id="backup-rows"></tbody></table></div></div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <div class="card h-100 border-success"><div class="card-header text-success"><i class="bi bi-calendar-check"></i> Planification Sauvegardes</div><div class="card-body small"><form action="/settings" method="post" class="mb-2"><div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="auto_backup" {% if settings.auto_backup %}checked{% endif %}> Backup Auto</div><div class="row g-2 mt-1"><div class="col-6"><label class="x-small">Intervalle (j)</label><input type="number" name="interval_days" class="form-control form-control-sm" value="{{settings.interval_days}}"></div><div class="col-6"><label class="x-small">Heure</label><input type="time" name="backup_time" class="form-control form-control-sm" value="{{settings.backup_time}}"></div></div><div class="input-group mt-2"><select name="cleanup_mode" class="form-select form-select-sm"><option value="count" {% if settings.cleanup_mode == 'count' %}selected{% endif %}>Garder X</option><option value="days" {% if settings.cleanup_mode == 'days' %}selected{% endif %}>Max X jours</option></select><input type="number" name="cleanup_value" class="form-control form-control-sm" value="{{settings.cleanup_value}}"></div><button class="btn btn-sm btn-primary w-100 mt-2">Sauver Paramètres Local</button></form><button class="btn btn-sm btn-outline-info w-100" data-bs-toggle="modal" data-bs-target="#extBackupModal"><i class="bi bi-cloud-upload"></i> Externalisation</button></div></div>
                            </div>
                        </div>
                        <div class="card border-secondary mb-3"><div class="card-header d-flex justify-content-between align-items-center py-2"><span class="small text-info"><i class="bi bi-journal-check"></i> Journal des Opérations (Sauvegardes & Maintenance)</span><button class="btn btn-sm btn-outline-secondary py-0" onclick="refreshMaintHistory()"><i class="bi bi-arrow-repeat"></i></button></div><div class="card-body p-2 bg-dark" style="max-height: 440px; overflow-y: auto;"><div id="backupHistoryList">{% for entry in history %}<div class="mb-2 pb-1 border-bottom border-secondary last-child-border-0"><span class="x-small text-info">{{ entry.timestamp }}</span><br><span style="font-size: 0.75rem;">{{ entry.report | safe }}</span></div>{% endfor %}{% if not history %}<span class="x-small text-muted">Aucun log disponible.</span>{% endif %}</div></div></div>
                    </div>

                    <div class="tab-pane fade" id="v-pills-security" role="tabpanel">
                        <div class="card border-warning mb-3"><div class="card-header text-warning"><i class="bi bi-shield-lock"></i> Accès Système</div><div class="card-body small"><button class="btn btn-sm btn-outline-warning w-100 mb-2" onclick="copyPwd()">Copier Pass Admin OWUI</button><button class="btn btn-sm btn-outline-danger w-100 mb-2" id="btn-bunkerweb" data-bs-toggle="modal" data-bs-target="#bunkerwebModal" disabled>🛡️ Sécurité WAF (BunkerWeb)</button><button class="btn btn-sm btn-outline-secondary w-100 mb-2" data-bs-toggle="collapse" data-bs-target="#ssh">Changer Pass Système</button><div class="collapse mt-2" id="ssh"><form action="/action/security/passwd" method="post" class="bg-dark p-2 rounded"><input type="password" name="current_password" class="form-control form-control-sm mb-1" placeholder="Actuel"><input type="password" name="new_password" class="form-control form-control-sm mb-1" placeholder="Nouveau"><input type="password" name="confirm_password" class="form-control form-control-sm mb-1" placeholder="Confirmer le nouveau"><button class="btn btn-sm btn-warning w-100">Valider</button></form></div></div></div>
                    </div>

                    <div class="tab-pane fade" id="v-pills-docker" role="tabpanel">
                        <div class="card border-secondary mb-3"><div class="card-header d-flex flex-column gap-2"><div class="d-flex justify-content-between align-items-center"><span><i class="bi bi-box"></i> Conteneurs Docker</span><button class="btn btn-sm btn-link text-secondary p-0" onclick="refreshContainers()"><i class="bi bi-arrow-repeat"></i></button></div><div class="d-flex gap-2"><select id="docker-sort-by" class="form-select form-select-sm bg-dark text-white border-secondary" onchange="renderContainers()"><option value="name">Nom</option><option value="cpu">CPU</option><option value="ram">RAM</option><option value="size">Taille</option><option value="volumes">Taille Volumes</option><option value="started_at">Démarrage</option></select><select id="docker-sort-order" class="form-select form-select-sm bg-dark text-white border-secondary" style="width: 80px;" onchange="renderContainers()"><option value="asc">Asc</option><option value="desc">Desc</option></select></div></div><div class="card-body p-0" style="height: 75vh; overflow-y: auto;"><ul class="list-group list-group-flush mb-0" id="container-list"></ul></div></div>
                    </div>

                </div>
            </div>
        </div>
        
        <!-- Modal Externalisation -->
        <div class="modal fade" id="extBackupModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content bg-dark text-light"><div class="modal-header border-secondary"><h5 class="modal-title"><i class="bi bi-cloud-arrow-up"></i> Externalisation des Sauvegardes</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><form action="/settings/external_backup" method="post"><div class="modal-body"><div class="mb-3"><label class="form-label text-info">Mode Actif</label><select name="ext_mode" class="form-select bg-dark text-light border-secondary"><option value="local" {% if settings.ext_mode == 'local' %}selected{% endif %}>Désactivé (Local Uniquement)</option><option value="sftp" {% if settings.ext_mode == 'sftp' %}selected{% endif %}>Serveur NAS (SFTP)</option><option value="rclone" {% if settings.ext_mode == 'rclone' %}selected{% endif %}>Cloud Grand Public (Rclone)</option></select></div><hr class="border-secondary"><div class="row"><div class="col-md-6"><h6 class="text-warning">Configuration SFTP (NAS)</h6><div class="mb-2"><input type="text" name="sftp_host" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Hôte (IP/Domaine)" value="{{settings.sftp_host}}"></div><div class="mb-2"><input type="text" name="sftp_port" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Port (ex: 22)" value="{{settings.sftp_port}}"></div><div class="mb-2"><input type="text" name="sftp_user" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Utilisateur" value="{{settings.sftp_user}}"></div><div class="mb-2"><input type="password" name="sftp_pass" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Mot de passe" value="{{settings.sftp_pass}}"></div><div class="mb-2"><input type="text" name="sftp_path" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Chemin (ex: /backups/echo)" value="{{settings.sftp_path}}"></div></div><div class="col-md-6"><h6 class="text-primary">Configuration Rclone (Cloud)</h6><div class="mb-2 d-flex gap-2"><select id="rcloneRemoteSelect" name="rclone_remote_name" class="form-select form-select-sm bg-dark text-light border-secondary"><option value="">-- Choisir un profil Rclone --</option>{% for r in rclone_remotes %}<option value="{{ r }}" {% if settings.rclone_remote and settings.rclone_remote.split(':')[0] == r %}selected{% endif %}>{{ r }}</option>{% endfor %}</select><button type="button" class="btn btn-sm btn-outline-primary text-nowrap" data-bs-toggle="modal" data-bs-target="#rcloneWizardModal">➕ Ajouter</button><button type="button" class="btn btn-sm btn-outline-danger text-nowrap" onclick="deleteRcloneProfile()" title="Supprimer le profil sélectionné">✕</button></div><div class="mb-2"><input type="text" name="rclone_subpath" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Dossier ou bucket/chemin (ex: mon-bucket/echo)" value="{{settings.rclone_remote.split(':')[1] if ':' in settings.rclone_remote else ''}}"></div><small class="text-muted d-block" style="font-size:0.75rem;">L'authentification OAuth et la génération du fichier de configuration sont désormais entièrement gérées par l'assistant.</small></div></div><hr class="border-secondary"><h6 class="text-danger">Élagage Distant de l'Externalisation</h6><div class="input-group mt-2 w-75"><select name="ext_cleanup_mode" class="form-select form-select-sm bg-dark text-light border-secondary"><option value="count" {% if settings.ext_cleanup_mode == 'count' %}selected{% endif %}>Garder X</option><option value="days" {% if settings.ext_cleanup_mode == 'days' %}selected{% endif %}>Max X jours</option></select><input type="number" name="ext_cleanup_value" class="form-control form-control-sm bg-dark text-light border-secondary" value="{{settings.ext_cleanup_value}}"></div><small class="text-muted d-block mt-1">S'applique uniquement au dossier distant configuré ci-dessus.</small></div><div class="modal-footer border-secondary"><button type="submit" class="btn btn-success">Enregistrer les paramètres</button></div></form></div></div></div>
        
        <!-- Modal Wizard Rclone -->
        <div class="modal fade" id="rcloneWizardModal" tabindex="-1" style="background: rgba(0,0,0,0.8);"><div class="modal-dialog modal-lg"><div class="modal-content bg-dark text-light border-primary"><div class="modal-header border-secondary"><h5 class="modal-title text-primary"><i class="bi bi-magic"></i> Assistant de Profil Cloud (Rclone)</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><form action="/action/rclone/create" method="post" enctype="multipart/form-data" autocomplete="off" onsubmit="showLoader('Création du profil Cloud en cours...')"><div class="modal-body"><div class="row mb-3"><div class="col-md-6"><label class="form-label x-small" data-bs-toggle="tooltip" title="Identifiant unique du profil Rclone. Utilisé pour référencer cette connexion dans la configuration.">Nom du Profil <i class="bi bi-info-circle"></i></label><input type="text" name="remote_name" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="ex: MonGoogleDrive" required pattern="[a-zA-Z0-9_\\-]+"></div><div class="col-md-6"><label class="form-label x-small" data-bs-toggle="tooltip" title="Type de stockage distant. Chaque fournisseur requiert des informations de connexion différentes.">Fournisseur <i class="bi bi-info-circle"></i></label><select id="wizardProvider" name="provider" class="form-select form-select-sm bg-dark text-light border-secondary" onchange="toggleWizardFields()"><option value="drive">Google Drive</option><option value="s3">S3 / R2 / B2 (Standard)</option><option value="webdav">WebDAV (Nextcloud, kDrive...)</option></select></div></div><hr class="border-secondary">
        
        <!-- Section S3 -->
        <div id="wiz_s3" style="display:none;"><div class="row g-2 mb-2"><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Sélectionnez le fournisseur de stockage objet S3 compatible.">Provider <i class="bi bi-info-circle"></i></label><select name="s3_provider" class="form-select form-select-sm bg-dark text-light border-secondary"><option value="AWS">Amazon S3</option><option value="Cloudflare">Cloudflare R2</option><option value="Backblaze">Backblaze B2</option><option value="Other">Autre S3 Compatible</option></select></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Région S3 (ex: eu-west-3, auto pour R2). Optionnel selon le fournisseur.">Région <i class="bi bi-info-circle"></i></label><input type="text" name="s3_region" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="ex: eu-west-3 ou auto"></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Clé d'accès publique fournie par votre fournisseur S3.">Access Key ID <i class="bi bi-info-circle"></i></label><input type="text" name="s3_access_key" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="AKIAIOSFODNN7EXAMPLE"></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Clé secrète associée. Ne sera jamais réaffichée après enregistrement.">Secret Access Key <i class="bi bi-info-circle"></i></label><input type="password" name="s3_secret_key" autocomplete="new-password" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="wJalrXUtnFEMI/K7MDENG/bPxRfi"></div><div class="col-md-12 mt-2"><label class="x-small" data-bs-toggle="tooltip" title="URL du endpoint S3. Obligatoire pour R2, B2 et les services S3 non-AWS.">Endpoint URL <i class="bi bi-info-circle"></i></label><input type="text" name="s3_endpoint" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="https://s3.eu-west-1.amazonaws.com"></div></div></div>
        
        <!-- Section WebDAV -->
        <div id="wiz_webdav" style="display:none;"><div class="row g-2 mb-2"><div class="col-md-8"><label class="x-small" data-bs-toggle="tooltip" title="URL complète du point d'accès WebDAV de votre service Cloud.">URL du Serveur WebDAV <i class="bi bi-info-circle"></i></label><input type="text" name="webdav_url" autocomplete="off" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="https://nextcloud.exemple.com/remote.php/webdav/"></div><div class="col-md-4"><label class="x-small" data-bs-toggle="tooltip" title="Type de serveur WebDAV pour optimiser la compatibilité.">Type de serveur <i class="bi bi-info-circle"></i></label><select name="webdav_vendor" class="form-select form-select-sm bg-dark text-light border-secondary"><option value="nextcloud">Nextcloud</option><option value="owncloud">Owncloud</option><option value="other" selected>Autre (kDrive, etc.)</option></select></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Identifiant de connexion à votre service Cloud (email ou login).">Utilisateur <i class="bi bi-info-circle"></i></label><input type="text" name="webdav_user" autocomplete="off" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="utilisateur@email.com"></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Mot de passe d'application recommandé. Générez-le depuis les paramètres de sécurité de votre fournisseur.">Mot de passe / Token d'App <i class="bi bi-info-circle"></i></label><input type="password" name="webdav_pass" autocomplete="new-password" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Token ou mot de passe d'application"></div></div></div>
        
        
        <!-- Section Google Drive -->
        <div id="wiz_drive" style="display:block;">
            <ul class="nav nav-pills nav-fill mb-3" id="gdriveTabs" role="tablist">
                <li class="nav-item" role="presentation"><button class="nav-link active py-1 x-small" data-bs-toggle="pill" data-bs-target="#gdriveToken" type="button" role="tab">Méthode 1 : Token Local (Facile)</button></li>
                <li class="nav-item" role="presentation"><button class="nav-link py-1 x-small" data-bs-toggle="pill" data-bs-target="#gdriveJson" type="button" role="tab">Méthode 2 : Service Account (Pro)</button></li>
            </ul>
            <div class="tab-content">
                <div class="tab-pane fade show active" id="gdriveToken" role="tabpanel">
                    <div class="alert alert-dark border-secondary p-2 small mb-2 text-muted">
                        <i class="bi bi-info-circle text-info"></i> Google a interdit la copie manuelle de code depuis 2022. Pour autoriser Google Drive :<br>
                        1. Téléchargez Rclone sur votre PC Windows : <a href="https://rclone.org/downloads/" target="_blank" class="text-primary">Lien Officiel</a><br>
                        2. Ouvrez un terminal (PowerShell) sur votre PC et tapez : <code class="text-warning">rclone authorize "drive"</code><br>
                        3. Rclone ouvrira votre navigateur. Connectez-vous à Google et validez.<br>
                        4. Rclone affichera un bloc de texte (JSON) dans votre terminal. Copiez-le et collez-le ci-dessous.
                    </div>
                    <label class="x-small text-primary">Token OAuth (Coller le bloc JSON retourné par votre terminal local) :</label>
                    <textarea name="drive_token" class="form-control form-control-sm bg-dark text-light border-primary" rows="4" placeholder='{"access_token":"ya29.a0A...","token_type":"Bearer","refresh_token":"1//0f..."}'></textarea>
                </div>
                <div class="tab-pane fade" id="gdriveJson" role="tabpanel">
                    <div class="alert alert-dark border-secondary p-2 small mb-2 text-muted">
                        <i class="bi bi-shield-check text-success"></i> Recommandé pour les serveurs sans écran. Pas d'expiration de token.
                    </div>
                    <label class="x-small text-primary">Fichier JSON du Compte de Service (Google Cloud) :</label>
                    <input type="file" name="drive_sa_file" class="form-control form-control-sm bg-dark text-light border-primary mb-2" accept=".json">
                    <label class="x-small text-primary">ID du Dossier Partagé Google Drive (Requis pour un Service Account) :</label>
                    <input type="text" name="drive_root_folder_id" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="ex: 1A2b3C4d5E6f7G8h9I0j...">
                </div>
            </div>
        </div>
        
        </div><div class="modal-footer border-secondary"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button><button type="submit" class="btn btn-primary">Générer le Profil</button></div></form></div></div></div>
        
        <!-- Modal Auth Settings -->
        <div class="modal fade" id="authSettingsModal" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content bg-dark text-light">
                    <div class="modal-header border-secondary">
                        <h5 class="modal-title text-primary"><i class="bi bi-shield-lock"></i> Paramètres de Sécurité SSO</h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                    </div>
                    <form action="/action/auth/settings" method="post">
                        <div class="modal-body">
                            <h6 class="text-warning">Complexité des mots de passe (Définitifs & Temporaires)</h6>
                            <div class="row g-2 mb-3">
                                <div class="col-md-4">
                                    <label class="form-label x-small text-muted">Longueur minimale</label>
                                    <input type="number" name="pwd_min_length" class="form-control form-control-sm bg-dark text-light border-secondary" value="{{ auth_settings.pwd_min_length }}" min="8" max="64">
                                </div>
                                <div class="col-md-8 d-flex align-items-center flex-wrap gap-3 mt-4">
                                    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="pwd_require_upper" id="reqUp" {{ 'checked' if auth_settings.pwd_require_upper }}><label class="form-check-label x-small" for="reqUp">Majuscule</label></div>
                                    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="pwd_require_lower" id="reqLow" {{ 'checked' if auth_settings.pwd_require_lower }}><label class="form-check-label x-small" for="reqLow">Minuscule</label></div>
                                    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="pwd_require_digit" id="reqDig" {{ 'checked' if auth_settings.pwd_require_digit }}><label class="form-check-label x-small" for="reqDig">Chiffre</label></div>
                                    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="pwd_require_special" id="reqSpec" {{ 'checked' if auth_settings.pwd_require_special }}><label class="form-check-label x-small" for="reqSpec">Spécial</label></div>
                                </div>
                            </div>
                            <hr class="border-secondary">
                            <h6 class="text-info">Expirations & Délais</h6>
                            <div class="row g-2">
                                <div class="col-md-4">
                                    <label class="form-label x-small text-muted" data-bs-toggle="tooltip" title="Durée (h) de validité des MDP temporaires.">Expiration Temp. (h)</label>
                                    <input type="number" name="temp_pass_hours" class="form-control form-control-sm bg-dark text-light border-secondary" value="{{ auth_settings.temp_pass_hours }}" min="0">
                                </div>
                                <div class="col-md-4">
                                    <label class="form-label x-small text-muted" data-bs-toggle="tooltip" title="Tolérance de vérification du code OTP (fenêtres de 30s).">Tolérance OTP</label>
                                    <input type="number" name="totp_tolerance" class="form-control form-control-sm bg-dark text-light border-secondary" value="{{ auth_settings.totp_tolerance }}" min="0" max="5">
                                </div>
                                <div class="col-md-4">
                                    <label class="form-label x-small text-muted" data-bs-toggle="tooltip" title="Durée maximale de la session SSO.">Expiration Session</label>
                                    <select name="session_timeout" class="form-select form-select-sm bg-dark text-light border-secondary">
                                        <option value="0" {% if auth_settings.session_timeout == 0 %}selected{% endif %}>Infinie (Navigateur)</option>
                                        <option value="12" {% if auth_settings.session_timeout == 12 %}selected{% endif %}>12 heures</option>
                                        <option value="24" {% if auth_settings.session_timeout == 24 %}selected{% endif %}>24 heures</option>
                                        <option value="168" {% if auth_settings.session_timeout == 168 %}selected{% endif %}>1 Semaine</option>
                                        <option value="720" {% if auth_settings.session_timeout == 720 %}selected{% endif %}>1 Mois</option>
                                        <option value="2160" {% if auth_settings.session_timeout == 2160 %}selected{% endif %}>3 Mois</option>
                                    </select>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer border-secondary">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>
                            <button type="submit" class="btn btn-success">Enregistrer</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <!-- Modal BunkerWeb -->
        <div class="modal fade" id="bunkerwebModal" tabindex="-1"><div class="modal-dialog modal-xl"><div class="modal-content bg-dark text-light"><div class="modal-header border-secondary"><h5 class="modal-title text-danger"><i class="bi bi-shield-lock-fill"></i> Gestion BunkerWeb (WAF)</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><div class="modal-body">
            <ul class="nav nav-tabs border-secondary mb-3" id="bwTabs" role="tablist">
                <li class="nav-item" role="presentation"><button class="nav-link active py-1 small" data-bs-toggle="tab" data-bs-target="#bwBan" type="button" role="tab" onclick="refreshBwListBans()">Bannissement</button></li>
                <li class="nav-item" role="presentation"><button class="nav-link py-1 small" data-bs-toggle="tab" data-bs-target="#bwLogs" type="button" role="tab" onclick="refreshBwLogs()">Logs d'Attaques</button></li>
            </ul>
            <div class="tab-content">
                <div class="tab-pane fade show active" id="bwBan" role="tabpanel">
                    <div class="row mb-2"><div class="col-md-8 offset-md-2 text-center">
                        <div class="input-group"><input type="text" id="bwIp" class="form-control bg-dark text-light border-secondary" placeholder="Adresse IP (ex: 192.168.1.50)"><button class="btn btn-danger" onclick="bwAction('ban')">Bannir</button><button class="btn btn-success" onclick="bwAction('unban')">Débannir</button></div>
                        <div id="bwResult" class="small mt-1 text-warning font-monospace" style="white-space: pre-wrap; text-align: left;"></div>
                    </div></div>
                    <hr class="border-secondary mb-2 mt-1">
                    <div class="d-flex justify-content-between mb-2 align-items-center">
                        <h6 class="text-danger mb-0"><i class="bi bi-shield-x"></i> IPs actuellement bannies</h6>
                        <button class="btn btn-sm btn-outline-secondary py-0" onclick="refreshBwListBans()"><i class="bi bi-arrow-repeat"></i></button>
                    </div>
                    <div class="bg-black p-2 rounded border border-secondary" style="height: 350px; overflow-y: auto; font-family: monospace; font-size: 0.8rem;" id="bwBansContent">Chargement...</div>
                </div>
                <div class="tab-pane fade" id="bwLogs" role="tabpanel">
                    <div class="d-flex justify-content-between mb-2"><input type="text" id="bwLogSearch" class="form-control form-control-sm bg-dark text-light border-secondary w-50" placeholder="Filtrer les logs..." onkeyup="filterBwLogs()"><button class="btn btn-sm btn-outline-secondary" onclick="refreshBwLogs()"><i class="bi bi-arrow-repeat"></i> Rafraîchir</button></div>
                    <div class="bg-black p-2 rounded border border-secondary" style="height: 400px; overflow-y: auto; font-family: monospace; font-size: 0.8rem;" id="bwLogContent">Chargement...</div>
                </div>
            </div>
        </div></div></div></div>

    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            const backupSwitch = document.querySelector('input[name="auto_backup"]');
            const cleanupInput = document.getElementById('cleanup_hour_input');
            const warningMsg = document.getElementById('maint-sync-warning');

            function syncUI() {
                if (backupSwitch && backupSwitch.checked) {
                    cleanupInput.setAttribute('disabled', 'disabled');
                    cleanupInput.style.opacity = '0.5';
                    warningMsg.style.display = 'block';
                } else {
                    cleanupInput.removeAttribute('disabled');
                    cleanupInput.style.opacity = '1';
                    warningMsg.style.display = 'none';
                }
            }
            if(backupSwitch) {
                backupSwitch.addEventListener('change', syncUI);
                syncUI();
            }
        });

        const initialTime = new Date('{{ server_time_iso }}');
        function initClock() {
            let now = initialTime;
            setInterval(() => {
                now.setSeconds(now.getSeconds() + 1);
                document.getElementById('clock').innerText = now.toLocaleTimeString();
            }, 1000);
        }
        function toggleWizardFields() {
            const provider = document.getElementById('wizardProvider').value;
            ['drive', 's3', 'webdav'].forEach(p => {
                const el = document.getElementById('wiz_' + p);
                if (el) {
                    const active = (provider === p);
                    el.style.display = active ? 'block' : 'none';
                    el.querySelectorAll('input, select, textarea').forEach(inp => { inp.disabled = !active; });
                }
            });
        }
        toggleWizardFields();
        function deleteRcloneProfile() {
            const sel = document.getElementById('rcloneRemoteSelect');
            const name = sel.value;
            if (!name) { alert('Sélectionnez un profil à supprimer.'); return; }
            if (!confirm('Supprimer définitivement le profil Rclone « ' + name + ' » ?')) return;
            const f = document.createElement('form');
            f.method = 'POST'; f.action = '/action/rclone/delete';
            const i = document.createElement('input');
            i.type = 'hidden'; i.name = 'remote_name'; i.value = name;
            f.appendChild(i); document.body.appendChild(f); f.submit();
        }
        async function refreshMaintHistory() {
            try {
                const r = await fetch('/api/maint/history');
                const entries = await r.json();
                const html = entries.length ? entries.map(e => `<div class="mb-2 pb-1 border-bottom border-secondary last-child-border-0"><span class="x-small text-info">${e.timestamp}</span><br><span style="font-size: 0.75rem;">${e.report}</span></div>`).join('') : '<span class="x-small text-muted">Aucun log disponible.</span>';
                const el1 = document.getElementById('historyLogList');
                const el2 = document.getElementById('backupHistoryList');
                if (el1) el1.innerHTML = html;
                if (el2) el2.innerHTML = html;
            } catch (e) {}
        }
        window._wasTaskRunning = false;
        async function pollTaskStatus() {
            try {
                const r = await fetch('/api/task_status');
                const state = await r.json();
                const card = document.getElementById('task-progress-card');
                const statusEl = document.getElementById('task-progress-status');
                const elapsedEl = document.getElementById('task-progress-elapsed');
                const pctEl = document.getElementById('task-progress-pct');
                const barEl = document.getElementById('task-progress-bar');
                const btnBck = document.getElementById('btn-manual-backup');
                const btnPrn = document.getElementById('btn-run-pruning');
                const btnCon = document.getElementById('btn-consolidate');
                if (state.is_running) {
                    window._wasTaskRunning = true;
                    if (card) card.style.display = 'block';
                    if (statusEl) statusEl.innerText = state.status || 'Opération en cours...';
                    const pct = Math.min(100, Math.max(0, parseFloat(state.percent) || 0));
                    if (pctEl) pctEl.innerText = pct.toFixed(1).replace(/\\.0$/, '') + '%';
                    if (barEl) {
                        barEl.style.width = pct + '%';
                        barEl.className = 'progress-bar progress-bar-striped progress-bar-animated ' + (state.task_type === 'backup' ? 'bg-success' : (state.task_type === 'consolidate' ? 'bg-warning' : 'bg-info'));
                    }
                    if (elapsedEl && state.start_time) {
                        const sec = Math.max(0, Math.floor(Date.now() / 1000 - state.start_time));
                        elapsedEl.innerText = sec >= 60 ? `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, '0')}s` : `${sec}s`;
                    }
                    if (btnBck) btnBck.disabled = true;
                    if (btnPrn) btnPrn.disabled = true;
                    if (btnCon) btnCon.disabled = true;
                    if (state.task_type === 'backup') refreshBackups();
                } else if (window._wasTaskRunning) {
                    window._wasTaskRunning = false;
                    if (card) card.style.display = 'none';
                    if (btnBck) btnBck.disabled = false;
                    if (btnPrn) btnPrn.disabled = false;
                    if (btnCon) btnCon.disabled = false;
                    refreshBackups();
                    refreshMaintHistory();
                }
            } catch (e) {}
        }
        setInterval(pollTaskStatus, 2000);
        pollTaskStatus();

        function showLoader(m='Action en cours...'){document.getElementById('loader-msg').innerText=m;document.getElementById('loader').style.display='flex'}
        async function refreshAuthUsers(){try{const r=await fetch('/api/auth/users');const d=await r.json();document.getElementById('auth-users-list').innerHTML=d.map(u=>{let t='';if(u.status==='Pending'&&u.expires){const e=new Date(u.expires*1000);t=`<br><small class="${e<new Date()?'text-danger':'text-info'}">⏳ Exp. ${e.toLocaleDateString()} ${e.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</small>`;}else if(u.status==='Active'&&u.last_enrollment){const l=new Date(u.last_enrollment*1000);t=`<br><small class="text-success">✅ Enrôlé le ${l.toLocaleDateString()}</small>`;}const statusBadge=u.status==='Pending'?'<span class="badge bg-warning text-dark">En attente d\\'enrôlement</span>':'<span class="badge bg-success">Actif (MFA)</span>';const disableMsg = u.email === 'admin@echo.local' ? 'DANGER : La désactivation coupera l\\'accès externe à admin@echo.local. Seul un accès local via le Fallback (Port 3001) permettra de le réimporter. Confirmer ?' : 'Désactiver l\\'accès externe de ce compte ? Son historique interne ECHO sera conservé.';const nameDisplay = u.name ? u.name : 'Aucun nom défini';return `<tr><td class="align-middle">${u.email}<br><small class="text-secondary">${nameDisplay}</small></td><td class="align-middle">${statusBadge}${t}</td><td class="text-end align-middle"><form action="/action/auth/rename" method="post" class="d-inline" onsubmit="let n=prompt('Nouveau nom pour ${u.email}:', '${u.name||''}'); if(n!==null){this.name.value=n; return true;} return false;"><input type="hidden" name="email" value="${u.email}"><input type="hidden" name="name" value=""><button class="btn btn-sm btn-outline-info py-0 me-1" title="Modifier le Nom">✏️</button></form><form action="/action/auth/reset" method="post" class="d-inline" onsubmit="return confirm('Forcer la réinitialisation MFA pour cet utilisateur ?')"><input type="hidden" name="email" value="${u.email}"><button class="btn btn-sm btn-warning py-0 me-1" title="Réinitialiser MFA">🔑</button></form><form action="/action/auth/disable" method="post" class="d-inline" onsubmit="return confirm('${disableMsg}')"><input type="hidden" name="email" value="${u.email}"><button class="btn btn-sm btn-outline-warning py-0 me-1" title="Désactiver l'accès WAN">🚫</button></form><form action="/action/auth/delete" method="post" class="d-inline" onsubmit="return confirm('ATTENTION : Supprimer définitivement cet accès ET purger ses chats de l\\'espace ECHO ?')"><input type="hidden" name="email" value="${u.email}"><button class="btn btn-sm btn-danger py-0" title="Supprimer Totalement">🗑️</button></form></td></tr>`}).join('')}catch(e){console.error('Erreur AuthUsers:',e)}}
        
        // Cache GeoIP en mémoire pour ne pas appeler l'API à chaque rafraîchissement
        const geoIpCache = {};
        async function fetchGeoIp(ip) {
            if (!ip || ip.startsWith('192.168.') || ip.startsWith('10.') || ip.startsWith('172.') || ip === '127.0.0.1') return "Réseau Local";
            if (geoIpCache[ip]) return geoIpCache[ip];
            try { const r = await fetch(`http://ip-api.com/json/${ip}?fields=status,country,city`); const d = await r.json(); if (d.status === 'success') { geoIpCache[ip] = `${d.city}, ${d.country}`; return geoIpCache[ip]; } } catch(e) {}
            return "Inconnu";
        }
        
        async function refreshAuthSessions() {
            try {
                const r = await fetch('/api/auth/sessions'); const d = await r.json();
                if (d.error) { document.getElementById('auth-sessions-list').innerHTML = `<tr><td colspan="5" class="text-danger">${d.error}</td></tr>`; return; }
                if (d.sessions.length === 0) { document.getElementById('auth-sessions-list').innerHTML = `<tr><td colspan="5" class="text-center text-muted">Aucune session active.</td></tr>`; return; }
                
                let html = '';
                for (const s of d.sessions) {
                    const idShort = s.session_id.substring(0, 8) + '...';
                    const dev = s.device ? s.device : '?';
                    const osb = s.os ? `${s.browser} sur ${s.os}` : 'Inconnu';
                    const ip = s.ip_address ? s.ip_address.split(',').map(i => i.trim()).join('<br>') : 'N/A';
                    const expTxt = s.time_remaining_sec !== null ? 
                        (s.time_remaining_sec > 0 ? `<span class="text-success">${s.expires_dt}</span>` : `<span class="text-danger">Expiré</span>`) 
                        : `<span class="text-success">Infinie</span>`;
                    const trClass = s.time_remaining_sec !== null && s.time_remaining_sec <= 0 ? 'opacity-50' : '';
                    html += `<tr class="${trClass}" id="session-${s.session_id}"><td class="align-middle fw-bold text-info">${s.email}<br><small class="text-muted fw-normal font-monospace">${idShort}</small></td><td class="align-middle">${dev}<br><small class="text-muted font-monospace">${osb}</small></td><td class="align-middle font-monospace">${ip}<br><small class="text-muted" id="geo-${s.session_id}">Recherche...</small></td><td class="align-middle"><small>Créé: ${s.created_dt}<br>Exp: <span>${expTxt}</span></small></td><td class="text-end align-middle"><button class="btn btn-sm btn-danger py-0" onclick="revokeAuthSession('${s.session_id}')" title="Révoquer cette session"><i class="bi bi-trash"></i></button></td></tr>`;
                }
                document.getElementById('auth-sessions-list').innerHTML = html;
                
                // Fetch GeoIPs asynchronously
                for (const s of d.sessions) {
                    if (s.ip_address) {
                        const ips = s.ip_address.split(',').map(i => i.trim());
                        Promise.all(ips.map(fetchGeoIp)).then(locs => {
                            const el = document.getElementById(`geo-${s.session_id}`);
                            if (el) el.innerHTML = locs.join('<br>');
                        });
                    } else {
                        const el = document.getElementById(`geo-${s.session_id}`);
                        if (el) el.innerText = "N/A";
                    }
                }
            } catch(e) { console.error('Erreur AuthSessions:', e); document.getElementById('auth-sessions-list').innerHTML = `<tr><td colspan="5" class="text-danger">Erreur de chargement</td></tr>`; }
        }
        
        async function revokeAuthSession(sessionId) {
            if (!confirm('Révoquer cette session immédiatement ?')) return;
            try {
                const r = await fetch('/action/auth/session/revoke', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({session_id: sessionId}) });
                const d = await r.json();
                if (d.status === 'success') {
                    const row = document.getElementById(`session-${sessionId}`);
                    if (row) row.remove();
                } else { alert('Erreur: ' + d.error); }
            } catch(e) { alert('Erreur réseau'); }
        }
        
        async function revokeAllAuthSessions() {
            if (!confirm("DANGER : Vous allez déconnecter de force absolument TOUT LE MONDE (toutes les sessions SSO) de l'infrastructure ECHO. Confirmez-vous cette action ?")) return;
            showLoader('Purge globale des sessions...');
            try {
                const r = await fetch('/action/auth/session/revoke_all', { method: 'POST' });
                const d = await r.json();
                if (d.error) alert('Erreur: ' + d.error);
            } catch(e) { alert('Erreur réseau'); }
            finally {
                document.getElementById('loader').style.display = 'none';
                refreshAuthSessions();
            }
        }

        async function refreshUsers(){const r=await fetch('/api/user_stats');const d=await r.json();document.getElementById('user-list').innerHTML=d.map(u=>{const isSys=u.email.endsWith('@echo.local');const roleBtn=isSys?(u.role==='admin'?'<span class="badge bg-danger me-2">Admin Sys</span>':'<span class="badge bg-secondary me-2">System</span>'):`<form action="/action/user_role/${u.id}" method="post" class="d-inline me-2" onsubmit="return confirm('Confirmer la bascule du rôle Administrateur pour ${u.name} ?')"><input type="hidden" name="new_role" value="${u.role==='admin'?'user':'admin'}"><button type="submit" class="btn btn-sm py-0 ${u.role==='admin'?'btn-danger':'btn-outline-secondary'}" style="font-size:0.7rem;" data-bs-toggle="tooltip" title="Basculer le rôle">${u.role==='admin'?'Admin':'User'}</button></form>`;const purgeBtn=isSys?'':`<form action="/action/auth_reset/${u.id}" method="post" class="d-inline" onsubmit="return confirm('Purger les accès distants (Google/API) de ${u.name} ?')"><button type="submit" class="btn btn-sm btn-link text-danger p-0" data-bs-toggle="tooltip" title="Purger Tokens/Clés"><i class="bi bi-shield-lock"></i></button></form>`;return `<tr><td class="ps-3">${u.name}</td><td>${u.email}</td><td class="text-center"><span class="badge bg-primary">${u.chat_count}</span></td><td class="text-end pe-3">${roleBtn}${purgeBtn}</td></tr>`}).join('');var t=[].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));t.map(function(e){return new bootstrap.Tooltip(e)})}
        async function refreshBackups(){const r=await fetch('/api/backups');const d=await r.json();document.getElementById('backup-rows').innerHTML=d.map(b=>`<tr><td class="ps-3 text-truncate" style="max-width:200px;">${b.name}</td><td>${b.date}</td><td><span class="badge bg-secondary">${b.size}</span></td><td class="text-end pe-3"><div class="btn-group"><a href="/download/${b.name}" class="btn btn-sm text-primary"><i class="bi bi-download"></i></a><form action="/action/restore" method="post" onsubmit="return confirm('RESTAURER ?')" class="d-inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-sm text-warning">↺</button></form><form action="/action/delete_backup" method="post" class="d-inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-sm btn-danger">×</button></form></div></td></tr>`).join('')}
        let containersData = [];
        async function refreshContainers() { try { const r = await fetch('/api/containers'); containersData = await r.json(); renderContainers(); } catch(e) {} }
        function renderContainers() { const sortBy = document.getElementById('docker-sort-by')?.value || 'name'; const order = document.getElementById('docker-sort-order')?.value === 'asc' ? 1 : -1; let d = [...containersData]; d.sort((a, b) => { let valA, valB; if (sortBy === 'cpu') { valA = parseFloat(a.cpu) || 0; valB = parseFloat(b.cpu) || 0; } else if (sortBy === 'ram') { valA = parseFloat(a.ram) || 0; valB = parseFloat(b.ram) || 0; } else if (sortBy === 'size') { valA = a.size_bytes || 0; valB = b.size_bytes || 0; } else if (sortBy === 'volumes') { valA = a.total_volumes_bytes || 0; valB = b.total_volumes_bytes || 0; } else if (sortBy === 'started_at') { valA = new Date(a.started_at).getTime() || 0; valB = new Date(b.started_at).getTime() || 0; } else { valA = a.name.toLowerCase(); valB = b.name.toLowerCase(); } if (valA < valB) return -1 * order; if (valA > valB) return 1 * order; return 0; }); document.getElementById('container-list').innerHTML = d.map(c => { const cpuVal = parseFloat(c.cpu) || 0; const ramVal = parseFloat(c.ram) || 0; const diskVal = c.size_perc || 0; const cpuColor = cpuVal > 80 ? 'bg-danger' : (cpuVal > 50 ? 'bg-warning' : 'bg-info'); const ramColor = ramVal > 85 ? 'bg-danger' : (ramVal > 60 ? 'bg-warning' : 'bg-primary'); const diskColor = diskVal > 10 ? 'bg-danger' : (diskVal > 5 ? 'bg-warning' : 'bg-success'); let volsHtml = ''; if (c.volumes && c.volumes.length > 0) { volsHtml = `<div class="mt-2 p-2 rounded bg-dark border border-secondary" style="font-size: 0.70rem;"><div class="mb-2 text-light fw-bold"><i class="bi bi-hdd-network text-info"></i> Volumes attachés</div><ul class="list-unstyled mb-0 ms-1">${c.volumes.map(v => { if (v.type === 'bind') return `<li class="mb-1 d-flex justify-content-between"><span class="text-truncate" style="max-width:250px;" title="${v.dest}"><i class="bi bi-folder2 text-secondary ms-2"></i> ${v.dest}</span><span>${v.size_str}</span></li>`; const vColor = v.size_perc > 10 ? 'bg-danger' : (v.size_perc > 5 ? 'bg-warning' : 'bg-success'); return `<li class="mb-2"><div class="d-flex justify-content-between mb-1 text-muted"><span class="text-truncate" style="max-width:200px;" title="${v.dest}"><i class="bi bi-folder2 text-secondary ms-2"></i> ${v.dest}</span><span>${v.size_str}</span></div><div class="progress" style="height: 3px; background-color: #2b3035;"><div class="progress-bar ${vColor}" style="width: ${Math.min(v.size_perc, 100)}%"></div></div></li>`; }).join('')}</ul></div>`; } return `<li class="list-group-item bg-transparent small d-flex flex-column justify-content-center border-secondary py-2"><div class="d-flex justify-content-between align-items-center mb-2"><span class="fw-bold text-truncate text-light" style="max-width:180px;"><i class="bi bi-box"></i> ${c.name}</span><div class="d-flex align-items-center gap-2"><span class="badge ${c.status.startsWith('Up')?'bg-success':'bg-danger'}">${c.status}</span><form action="/action/restart" method="post" class="m-0"><input type="hidden" name="container" value="${c.id}"><button class="btn btn-sm btn-link text-secondary p-0" title="Redémarrer"><i class="bi bi-arrow-clockwise"></i></button></form></div></div><div class="row gx-3 align-items-center text-muted" style="font-size: 0.75rem;"><div class="col-4"><div class="d-flex justify-content-between mb-1"><span>CPU</span><span>${c.cpu}</span></div><div class="progress" style="height: 4px;"><div class="progress-bar ${cpuColor}" style="width: ${Math.min(cpuVal, 100)}%"></div></div></div><div class="col-4"><div class="d-flex justify-content-between mb-1"><span>RAM</span><span>${c.ram}</span></div><div class="progress" style="height: 4px;"><div class="progress-bar ${ramColor}" style="width: ${Math.min(ramVal, 100)}%"></div></div></div><div class="col-4"><div class="d-flex justify-content-between mb-1"><span>Taille</span><span>${c.size}</span></div><div class="progress" style="height: 4px;"><div class="progress-bar ${diskColor}" style="width: ${Math.min(diskVal, 100)}%"></div></div></div></div>${volsHtml}</li>`; }).join(''); }
        async function copyPwd(){
            try {
                const r = await fetch('/api/admin/password');
                const d = await r.json();
                if (d.password === "N/A") { alert('Erreur : Mot de passe introuvable sur le serveur.'); return; }

                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(d.password);
                    alert('Copié dans le presse-papier !');
                } else {
                    let textArea = document.createElement("textarea");
                    textArea.value = d.password;
                    textArea.style.position = "fixed"; textArea.style.left = "-999999px"; textArea.style.top = "-999999px";
                    document.body.appendChild(textArea); textArea.focus(); textArea.select();
                    try { document.execCommand('copy'); alert('Copié ! (Méthode fallback)'); }
                    catch (err) { alert('Erreur lors de la copie manuelle'); }
                    document.body.removeChild(textArea);
                }
            } catch (e) { alert('Erreur réseau lors de la récupération du mot de passe.'); }
        }
        setInterval(async()=>{const r=await fetch('/api/stats');
const d=await r.json();document.getElementById('cpu').innerText=d.cpu_percent;document.getElementById('cpu-bar').style.width=d.cpu_percent+'%';document.getElementById('cpu-details').innerText=`${d.cpu_count} cœurs | Load: ${d.cpu_load.join(', ')}`;document.getElementById('ram').innerText=d.ram_percent;document.getElementById('ram-bar').style.width=d.ram_percent+'%';document.getElementById('ram-text').innerText=d.ram_used+' / '+d.ram_total;document.getElementById('disk-pct').innerText=d.disk_percent;document.getElementById('disk-bar').style.width=d.disk_percent+'%';document.getElementById('disk-text').innerText=d.disk_used+' / '+d.disk_total;const dc=d.disk_percent>85?'danger':d.disk_percent>70?'warning':'success';document.getElementById('disk-pct-wrap').className='fs-4 fw-bold text-'+dc;document.getElementById('disk-bar').className='progress-bar bg-'+dc},3000);
        initClock();refreshUsers();refreshAuthUsers();refreshAuthSessions();refreshContainers();refreshBackups();
        
        async function checkBunkerWeb() { try { const r = await fetch('/api/bunkerweb/status'); const d = await r.json(); const btn = document.getElementById('btn-bunkerweb'); if (btn && d.active) { btn.disabled = false; } } catch(e) {} }
        async function bwAction(action) { const ip = document.getElementById('bwIp').value; if (!ip) return alert('Saisissez une IP'); const resEl = document.getElementById('bwResult'); resEl.innerText = 'Exécution...'; const fd = new FormData(); fd.append('action', action); fd.append('ip', ip); try { const r = await fetch('/api/bunkerweb/action', {method: 'POST', body: fd}); const d = await r.json(); if(d.error) resEl.innerHTML = `<span class="text-danger">Erreur: ${d.error}</span>`; else resEl.innerHTML = `<span class="text-success">${d.output}</span>`; } catch(e) { resEl.innerText = 'Erreur réseau'; } }
        async function refreshBwLogs() { const el = document.getElementById('bwLogContent'); el.innerHTML = 'Chargement des derniers logs...'; try { const r = await fetch('/api/bunkerweb/logs'); const d = await r.json(); window.bwRawLogs = d; renderBwLogs(d); } catch(e) { el.innerText = 'Erreur lors du chargement des logs.'; } }
        function renderBwLogs(logs) { document.getElementById('bwLogContent').innerHTML = logs.map(l => `<div class="border-bottom border-secondary pb-1 mb-1">${l.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>`).join('') || '<div class="text-muted">Aucun log récent trouvé.</div>'; }
        function filterBwLogs() { if(!window.bwRawLogs) return; const term = document.getElementById('bwLogSearch').value.toLowerCase(); const filtered = window.bwRawLogs.filter(l => l.toLowerCase().includes(term)); renderBwLogs(filtered); }
        async function refreshBwListBans() { const el = document.getElementById('bwBansContent'); el.innerHTML = 'Chargement...'; try { const r = await fetch('/api/bunkerweb/list_bans'); const d = await r.json(); if(d.error) { el.innerHTML = `<span class="text-danger">Erreur: ${d.error}</span>`; return; } if(d.bans.length === 0) { el.innerHTML = '<span class="text-success">Aucune IP bannie actuellement.</span>'; return; } el.innerHTML = '<table class="table table-dark table-sm table-hover"><thead><tr><th>IP / Infos</th><th class="text-end">Action</th></tr></thead><tbody>' + d.bans.map(b => `<tr><td class="align-middle"><strong class="text-danger fs-5">${b.ip}</strong> <span class="badge bg-secondary">${b.country}</span> ${b.as_info ? `<span class="badge border border-info text-info ms-2"><i class="bi bi-hdd-network"></i> ${b.as_info}</span>` : ''}<br><small class="text-muted">${b.type}</small><br><small class="text-info">${b.details}</small></td><td class="text-end align-middle"><button class="btn btn-sm btn-success py-0" onclick="document.getElementById('bwIp').value='${b.ip}'; bwAction('unban');">Débannir</button></td></tr>`).join('') + '</tbody></table>'; } catch(e) { el.innerHTML = 'Erreur de chargement.'; } }
        checkBunkerWeb();
        document.getElementById('bunkerwebModal').addEventListener('shown.bs.modal', function() { refreshBwListBans(); });

        // Persistance des onglets (Sidebar + Sous-menus)
        document.addEventListener('shown.bs.tab', function (e) {
            const listId = e.target.closest('[role="tablist"]')?.id;
            if (listId) {
                const tabs = JSON.parse(localStorage.getItem('echoAdminTabs') || '{}');
                tabs[listId] = e.target.getAttribute('data-bs-target');
                localStorage.setItem('echoAdminTabs', JSON.stringify(tabs));
            }
        });
        const activeTabs = JSON.parse(localStorage.getItem('echoAdminTabs') || '{}');
        for (const [listId, target] of Object.entries(activeTabs)) {
            const btn = document.querySelector(`[data-bs-target="${target}"]`);
            if (btn) { try { new bootstrap.Tab(btn).show(); } catch(e) {} }
        }

        var tList=[].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]')).map(function(el){return new bootstrap.Tooltip(el)})
    </script>
</body></html>"""
