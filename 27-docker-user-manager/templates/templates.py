# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO USER MANAGER - HTML TEMPLATES (DARK MODE)
VERSION : 5.237.5.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.237.5.1: Compte à rebours dynamique 1s avant expiration d'archive, attribut download et bascule automatique à échéance.
# 5.237.2.1: Composant d'export asynchrone avec barre de progression temps réel (0-100%), bouton d'annulation et badge de téléchargement.
# 5.237.1.1: Correction SyntaxError JS sur apostrophe dans HTML_USER_DASHBOARD (raw string r"""..."""), calcul temps restant sur timestamp expires_at et GeoIP.
# 5.236.5.1: Interface Dark Mode Bootstrap 5.3 du portail utilisateur (my.{ECHO_DOMAIN}) avec lien direct vers ui.{ECHO_DOMAIN}.

__all__ = ["HTML_USER_DASHBOARD"]

HTML_USER_DASHBOARD = r"""
<!DOCTYPE html>
<html lang="fr" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ECHO Mon Compte {{ version }}</title>
    <link rel="icon" type="image/png" href="/static/favicon.png">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', sans-serif; font-size: 0.9rem; overflow-x: hidden; }
        .card { background-color: #161b22; border: 1px solid #30363d; margin-bottom: 20px; border-radius: 8px; }
        .card-header { background-color: #21262d; border-bottom: 1px solid #30363d; font-weight: 600; padding: 10px 15px; display: flex; justify-content: space-between; align-items: center; }
        .stat-val { font-size: 1.35rem; font-weight: bold; color: #f0f6fc; }
        .stat-label { font-size: 0.75rem; text-transform: uppercase; color: #8b949e; letter-spacing: 0.5px; }
        .nav-pills .nav-link { color: #c9d1d9; border-radius: 6px; padding: 10px 15px; margin-bottom: 6px; text-align: left; font-weight: 500; transition: all 0.2s; }
        .nav-pills .nav-link:hover { background-color: #21262d; color: #fff; }
        .nav-pills .nav-link.active { background-color: #238636; color: #fff; font-weight: 600; box-shadow: 0 2px 6px rgba(35, 134, 54, 0.35); }
        .pwd-rule { font-size: 0.82rem; color: #8b949e; margin-bottom: 3px; }
        .pwd-rule.valid { color: #3fb950; }
        .pwd-rule.invalid { color: #f85149; }
        .table-dark { --bs-table-bg: #161b22; }
    </style>
</head>
<body>
    <nav class="navbar navbar-dark bg-dark border-bottom border-secondary mb-4 px-3 py-2">
        <div class="container-fluid">
            <span class="navbar-brand fw-bold d-flex align-items-center">
                <img src="/static/favicon.png" width="24" height="24" class="me-2" onerror="this.style.display='none'">
                ECHO ESPACE PERSONNEL
                <span class="badge bg-success ms-2">{{ version }}</span>
                <span id="liveClock" class="badge bg-dark border border-secondary text-info ms-2 font-monospace">--:--:--</span>
            </span>
            <div class="d-flex align-items-center gap-2 flex-wrap">
                <span class="badge bg-secondary bg-opacity-25 border border-secondary text-light px-2 py-1">
                    <i class="bi bi-person-check-fill text-success me-1"></i>
                    <strong id="navDisplayName">{{ display_name }}</strong>
                    <span class="text-muted ms-1">({{ user_email }})</span>
                </span>
                <a href="https://ui.{{ domain }}" class="btn btn-sm btn-primary fw-bold">
                    <i class="bi bi-chat-dots-fill me-1"></i> Ouvrir ECHO AI (ui.{{ domain }})
                </a>
                {% if user_role == 'admin' %}
                <a href="https://am.{{ domain }}" class="btn btn-sm btn-outline-warning">
                    <i class="bi bi-sliders me-1"></i> Console Admin
                </a>
                {% endif %}
                <a href="/logout" class="btn btn-sm btn-outline-danger">
                    <i class="bi bi-box-arrow-right me-1"></i> Se déconnecter
                </a>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="alert alert-{{ category }} alert-dismissible fade show py-2">{{ message | safe }}<button type="button" class="btn-close" data-bs-dismiss="alert" style="padding: 0.75rem;"></button></div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <!-- BANDEAU KPI UTILISATEUR -->
        <div class="row g-3 mb-4">
            <div class="col-md-3">
                <div class="card p-3 h-100 mb-0">
                    <div class="d-flex justify-content-between">
                        <div>
                            <div class="stat-label">Taille du Profil (Disque)</div>
                            <div class="stat-val text-info" id="kpiProfileSize">{{ stats.profile_size_fmt }}</div>
                        </div>
                        <i class="bi bi-hdd-network fs-2 text-secondary"></i>
                    </div>
                    <div class="text-muted small mt-2" id="kpiProfileBreakdown">
                        Chats: {{ stats.chats_size_fmt }} &bull; Fichiers: {{ stats.files_size_fmt }} &bull; Skills: {{ stats.skills_size_fmt }} &bull; Codex: {{ stats.codex_size_fmt }}
                    </div>
                </div>
            </div>

            <div class="col-md-3">
                <div class="card p-3 h-100 mb-0">
                    <div class="d-flex justify-content-between">
                        <div>
                            <div class="stat-label">Conversations (Chats)</div>
                            <div class="stat-val text-success" id="kpiChatCount">{{ stats.chat_count }}</div>
                        </div>
                        <i class="bi bi-chat-left-text fs-2 text-secondary"></i>
                    </div>
                    <div class="text-muted small mt-2" id="kpiSessionDbCount">
                        {{ stats.session_db_count }} base(s) de session active(s) (session.db)
                    </div>
                </div>
            </div>

            <div class="col-md-3">
                <div class="card p-3 h-100 mb-0">
                    <div class="d-flex justify-content-between">
                        <div>
                            <div class="stat-label">Mémoire Vectorielle (Qdrant)</div>
                            <div class="stat-val text-warning" id="kpiQdrantTotal">{{ stats.qdrant_total_count }} <small class="fs-6 text-muted">pts</small></div>
                        </div>
                        <i class="bi bi-diagram-3 fs-2 text-secondary"></i>
                    </div>
                    <div class="text-muted small mt-2" id="kpiQdrantDetail">
                        Souvenirs LTM: {{ stats.qdrant_meta_count }} &bull; Segments RAG: {{ stats.qdrant_rag_count }}
                    </div>
                </div>
            </div>

            <div class="col-md-3">
                <div class="card p-3 h-100 mb-0">
                    <div class="d-flex justify-content-between">
                        <div>
                            <div class="stat-label">Temps Restant (Session SSO)</div>
                            <div class="stat-val font-monospace" id="kpiAuthCountdown">--</div>
                        </div>
                        <i class="bi bi-shield-lock fs-2 text-secondary"></i>
                    </div>
                    <div class="text-muted small mt-2" id="kpiAuthMeta">
                        {% if stats.current_session %}
                            {% if stats.current_session.is_trusted %}
                                <span class="badge bg-success bg-opacity-25 text-success border border-success">🛡️ Appareil de confiance</span>
                            {% else %}
                                <span class="badge bg-warning bg-opacity-25 text-warning border border-warning">⏳ Session standard</span>
                            {% endif %}
                            <span class="ms-1">Exp: {{ stats.current_session.expires_dt }}</span>
                        {% else %}
                            <span>Session SSO active</span>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>

        <div class="row">
            <!-- NAVIGATION LATÉRALE -->
            <div class="col-md-3 col-lg-2 mb-4">
                <div class="card p-2 sticky-top" style="top: 20px;">
                    <div class="nav flex-column nav-pills" id="userTabs" role="tablist" aria-orientation="vertical">
                        <button class="nav-link active" id="pill-profile" data-bs-toggle="pill" data-bs-target="#tab-profile" type="button" role="tab">
                            <i class="bi bi-person-gear me-2"></i> Mon Profil & Sécurité
                        </button>
                        <button class="nav-link" id="pill-sessions" data-bs-toggle="pill" data-bs-target="#tab-sessions" type="button" role="tab">
                            <i class="bi bi-laptop me-2"></i> Mes Connexions
                            <span class="badge bg-secondary float-end" id="navSessionsBadge">{{ stats.active_sessions_count }}</span>
                        </button>
                        <button class="nav-link" id="pill-data" data-bs-toggle="pill" data-bs-target="#tab-data" type="button" role="tab">
                            <i class="bi bi-file-earmark-zip me-2"></i> Mes Données & Compte
                        </button>
                    </div>
                    <hr class="border-secondary my-2">
                    <div class="px-2 py-1 small text-muted">
                        <div><i class="bi bi-fingerprint me-1"></i> ID Open WebUI :</div>
                        <div class="font-monospace text-truncate text-info" title="{{ user_id }}">{{ user_id }}</div>
                    </div>
                </div>
            </div>

            <!-- CONTENU DES ONGLETS -->
            <div class="col-md-9 col-lg-10">
                <div class="tab-content" id="userTabsContent">

                    <!-- ONGLET 1 : PROFIL & SÉCURITÉ -->
                    <div class="tab-pane fade show active" id="tab-profile" role="tabpanel">
                        <div class="row">
                            <div class="col-lg-5">
                                <div class="card">
                                    <div class="card-header text-info">
                                        <span><i class="bi bi-person-badge me-2"></i>Identité & Nom d'Affichage</span>
                                    </div>
                                    <div class="card-body">
                                        <form action="/action/profile/rename" method="POST">
                                            <div class="mb-3">
                                                <label class="form-label text-muted small">Adresse Email (Identifiant SSO immuable)</label>
                                                <input type="email" class="form-control bg-dark text-secondary border-secondary" value="{{ user_email }}" disabled readonly>
                                            </div>
                                            <div class="mb-3">
                                                <label class="form-label text-light small fw-bold">Nom complet / Pseudonyme</label>
                                                <input type="text" name="name" class="form-control bg-dark text-light border-secondary" value="{{ display_name }}" maxlength="120" required>
                                                <div class="form-text text-muted small">
                                                    Synchronisé instantanément entre le portail d'authentification SSO et votre espace Open WebUI.
                                                </div>
                                            </div>
                                            <button type="submit" class="btn btn-info w-100 fw-bold">
                                                <i class="bi bi-check2-circle me-1"></i> Enregistrer le nom
                                            </button>
                                        </form>
                                    </div>
                                </div>
                            </div>

                            <div class="col-lg-7">
                                <div class="card border-warning border-opacity-50">
                                    <div class="card-header text-warning">
                                        <span><i class="bi bi-key-fill me-2"></i>Modifier mon Mot de Passe SSO</span>
                                        <span class="badge bg-dark border border-warning text-warning">Argon2id + MFA</span>
                                    </div>
                                    <div class="card-body">
                                        <form action="/action/profile/password" method="POST" id="pwdForm">
                                            <div class="mb-3">
                                                <label class="form-label small fw-bold">Mot de passe actuel</label>
                                                <input type="password" name="current_password" class="form-control bg-dark text-light border-secondary" required autocomplete="current-password">
                                            </div>
                                            <div class="row g-2 mb-2">
                                                <div class="col-md-6">
                                                    <label class="form-label small fw-bold">Nouveau mot de passe</label>
                                                    <input type="password" name="new_password" id="newPasswordInput" class="form-control bg-dark text-light border-secondary" required autocomplete="new-password">
                                                </div>
                                                <div class="col-md-6">
                                                    <label class="form-label small fw-bold">Confirmer le nouveau mot de passe</label>
                                                    <input type="password" name="confirm_password" id="confirmPasswordInput" class="form-control bg-dark text-light border-secondary" required autocomplete="new-password">
                                                </div>
                                            </div>

                                            <div class="p-2 mb-3 rounded bg-dark border border-secondary">
                                                <div class="small fw-bold text-light mb-1">Critères de complexité requis :</div>
                                                <div class="pwd-rule" id="rule-len">❌ Au moins {{ auth_settings.pwd_min_length }} caractères</div>
                                                {% if auth_settings.pwd_require_upper %}
                                                <div class="pwd-rule" id="rule-upper">❌ Au moins une lettre majuscule (A-Z)</div>
                                                {% endif %}
                                                {% if auth_settings.pwd_require_lower %}
                                                <div class="pwd-rule" id="rule-lower">❌ Au moins une lettre minuscule (a-z)</div>
                                                {% endif %}
                                                {% if auth_settings.pwd_require_digit %}
                                                <div class="pwd-rule" id="rule-digit">❌ Au moins un chiffre (0-9)</div>
                                                {% endif %}
                                                {% if auth_settings.pwd_require_special %}
                                                <div class="pwd-rule" id="rule-spec">❌ Au moins un caractère spécial (!@#$...)</div>
                                                {% endif %}
                                                <div class="pwd-rule" id="rule-match">❌ Les deux mots de passe correspondent</div>
                                            </div>

                                            {% if has_totp %}
                                            <div class="mb-3">
                                                <label class="form-label small fw-bold text-info">
                                                    <i class="bi bi-shield-check me-1"></i>Code MFA TOTP (6 chiffres)
                                                </label>
                                                <input type="text" name="totp_code" class="form-control bg-dark text-light border-info font-monospace" placeholder="123456" pattern="[0-9]{6}" maxlength="6" inputmode="numeric" required autocomplete="one-time-code">
                                            </div>
                                            {% endif %}

                                            <div class="form-check mb-3">
                                                <input class="form-check-input" type="checkbox" name="revoke_others" id="revokeOthersCheck" checked>
                                                <label class="form-check-label small text-muted" for="revokeOthersCheck">
                                                    Déconnecter automatiquement tous mes autres appareils après le changement
                                                </label>
                                            </div>

                                            <button type="submit" class="btn btn-warning w-100 fw-bold text-dark" id="submitPwdBtn">
                                                <i class="bi bi-shield-lock-fill me-1"></i> Mettre à jour mon mot de passe
                                            </button>
                                        </form>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- ONGLET 2 : CONNEXIONS ACTIVES -->
                    <div class="tab-pane fade" id="tab-sessions" role="tabpanel">
                        <div class="card">
                            <div class="card-header">
                                <span><i class="bi bi-broadcast-pin me-2 text-success"></i>Mes Sessions SSO Actives</span>
                                <div class="d-flex gap-2">
                                    <button type="button" class="btn btn-sm btn-outline-light" onclick="fetchUserSessions()">
                                        <i class="bi bi-arrow-clockwise me-1"></i>Actualiser
                                    </button>
                                    <button type="button" class="btn btn-sm btn-outline-danger" onclick="revokeOtherSessions()">
                                        <i class="bi bi-slash-circle me-1"></i>Déconnecter les autres appareils
                                    </button>
                                </div>
                            </div>
                            <div class="card-body p-0">
                                <div class="table-responsive">
                                    <table class="table table-dark table-hover align-middle mb-0">
                                        <thead class="table-secondary text-dark">
                                            <tr>
                                                <th class="ps-3">Session / État</th>
                                                <th>Appareil & Navigateur</th>
                                                <th>Adresse IP</th>
                                                <th>Ouverture & Expiration</th>
                                                <th class="text-end pe-3">Action</th>
                                            </tr>
                                        </thead>
                                        <tbody id="userSessionsTableBody">
                                            <tr>
                                                <td colspan="5" class="text-center text-muted py-4">Chargement des sessions...</td>
                                            </tr>
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- ONGLET 3 : DONNÉES & COMPTE -->
                    <div class="tab-pane fade" id="tab-data" role="tabpanel">
                        <div class="row">
                            <div class="col-lg-6">
                                <div class="card border-info border-opacity-50">
                                    <div class="card-header text-info">
                                        <span><i class="bi bi-cloud-arrow-down-fill me-2"></i>Portabilité & Export Intégral (.tar.xz)</span>
                                        <span class="badge bg-info text-dark">Format JSON + Fichiers</span>
                                    </div>
                                    <div class="card-body">
                                        <p class="text-muted small">
                                            Téléchargez une archive compressée <code>.tar.xz</code> contenant l'intégralité des données de votre compte issues du domaine utilisateur d'<strong>echo-open-webui</strong> et de la base vectorielle <strong>Qdrant</strong> :
                                        </p>
                                        <ul class="small text-light mb-4">
                                            <li><code>manifest.json</code> : Métadonnées et statistiques complètes de l'export.</li>
                                            <li><code>webui/user_profile_and_chats.json</code> : Profil, historique complet des conversations, tags, notes et mémoires (<code>webui.db</code>).</li>
                                            <li><code>user_domain/</code> : Dossier personnel (<code>/users/{{ user_id }}</code>) incluant vos fichiers, productions Codex, skills, workflows et dumps JSON des bases <code>session.db</code>.</li>
                                            <li><code>qdrant/echo_meta_artifacts.json</code> & <code>qdrant/echo_session_rag.json</code> : Export JSON complet de vos souvenirs long-terme et segments vectoriels RAG.</li>
                                        </ul>
                                        <div id="exportActionContainer">
                                            <button type="button" class="btn btn-info w-100 fw-bold" id="btnStartExport" onclick="startExport()">
                                                <i class="bi bi-file-earmark-zip-fill me-1"></i> Préparer mon archive (.tar.xz)
                                            </button>
                                        </div>

                                        <!-- CARTE DE PROGRESSION TEMPS RÉEL -->
                                        <div id="exportProgressCard" class="card mt-3 border-info border-opacity-50 d-none" style="background-color: #0d1117;">
                                            <div class="card-body p-3">
                                                <div class="d-flex justify-content-between align-items-center mb-2">
                                                    <span class="small fw-bold text-info" id="exportProgressStep">Initialisation de l'export...</span>
                                                    <span class="badge bg-dark border border-info text-info font-monospace" id="exportProgressPercent">0%</span>
                                                </div>
                                                <div class="progress mb-2" style="height: 12px; background-color: #161b22;">
                                                    <div id="exportProgressBar" class="progress-bar progress-bar-striped progress-bar-animated bg-info" role="progressbar" style="width: 0%;"></div>
                                                </div>
                                                <div class="d-flex justify-content-between align-items-center mt-2">
                                                    <small class="text-muted" id="exportProgressSubtext">Traitement asynchrone sur le serveur...</small>
                                                    <button type="button" class="btn btn-sm btn-outline-danger" id="btnCancelExport" onclick="cancelExport()">
                                                        <i class="bi bi-x-circle me-1"></i>Annuler
                                                    </button>
                                                </div>
                                            </div>
                                        </div>

                                        <!-- CARTE D'ARCHIVE PRÊTE -->
                                        <div id="exportReadyCard" class="card mt-3 border-success border-opacity-50 d-none" style="background-color: #0d1117;">
                                            <div class="card-body p-3 text-center">
                                                <div class="text-success mb-2">
                                                    <i class="bi bi-check-circle-fill fs-3 me-2"></i>
                                                    <span class="fw-bold fs-6">Votre archive est prête !</span>
                                                </div>
                                                <p class="small text-muted mb-3" id="exportReadyInfo">Fichier compressé disponible au téléchargement.</p>
                                                <div class="d-flex gap-2">
                                                    <a href="/action/account/export/download" class="btn btn-success fw-bold flex-grow-1" id="btnDownloadExport" download onclick="onDownloadStarted()">
                                                        <i class="bi bi-download me-1"></i> Télécharger l'archive (<span id="exportReadySize">--</span>)
                                                    </a>
                                                    <button type="button" class="btn btn-outline-secondary" onclick="cancelExport()" title="Supprimer l'archive du serveur">
                                                        <i class="bi bi-trash3"></i>
                                                    </button>
                                                </div>
                                                <div class="mt-2 text-warning small font-monospace" id="exportCountdownText">
                                                    <i class="bi bi-hourglass-split me-1"></i>Expire dans : <span id="exportCountdownVal">--:--</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div class="col-lg-6">
                                <div class="card border-danger">
                                    <div class="card-header bg-danger bg-opacity-25 text-danger">
                                        <span><i class="bi bi-exclamation-octagon-fill me-2"></i>Zone de Danger — Suppression du Compte</span>
                                    </div>
                                    <div class="card-body">
                                        <p class="small text-light">
                                            Cette action supprime immédiatement votre compte de l'<strong>Auth Manager</strong> (révocation de vos accès SSO et de toutes vos sessions actives) ainsi que votre entrée utilisateur dans Open WebUI.
                                        </p>
                                        <div class="alert alert-warning py-2 small">
                                            <i class="bi bi-info-circle-fill me-1"></i>
                                            <strong>Purge automatique :</strong> Vos données applicatives (conversations, fichiers, souvenirs Qdrant, workflows) seront automatiquement purgées la nuit suivante par le cycle de maintenance de l'Admin Manager. Pensez à télécharger votre archive <code>.tar.xz</code> au préalable.
                                        </div>
                                        {% if user_email.endswith('@echo.local') %}
                                        <button class="btn btn-secondary w-100" disabled>
                                            <i class="bi bi-lock-fill me-1"></i> Compte système protégé (suppression désactivée)
                                        </button>
                                        {% else %}
                                        <button type="button" class="btn btn-outline-danger w-100 fw-bold" data-bs-toggle="modal" data-bs-target="#deleteAccountModal">
                                            <i class="bi bi-person-x-fill me-1"></i> Supprimer définitivement mon compte
                                        </button>
                                        {% endif %}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>
        </div>
    </div>

    <!-- MODALE DE SUPPRESSION DE COMPTE -->
    <div class="modal fade" id="deleteAccountModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content bg-dark border-danger">
                <div class="modal-header border-secondary bg-danger bg-opacity-25">
                    <h5 class="modal-title text-danger fw-bold">
                        <i class="bi bi-exclamation-triangle-fill me-2"></i>Confirmer la suppression du compte
                    </h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <form action="/action/account/delete" method="POST">
                    <div class="modal-body">
                        <p class="small text-light">
                            Vous êtes sur le point de supprimer le compte <strong>{{ user_email }}</strong>. Cette opération est <strong>irréversible</strong>.
                        </p>
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Saisissez <code>SUPPRIMER</code> en majuscules :</label>
                            <input type="text" name="confirm_text" class="form-control bg-dark text-danger border-danger font-monospace" placeholder="SUPPRIMER" required pattern="SUPPRIMER">
                        </div>
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Mot de passe actuel :</label>
                            <input type="password" name="current_password" class="form-control bg-dark text-light border-secondary" required autocomplete="current-password">
                        </div>
                        {% if has_totp %}
                        <div class="mb-2">
                            <label class="form-label small fw-bold text-info">Code MFA TOTP (6 chiffres) :</label>
                            <input type="text" name="totp_code" class="form-control bg-dark text-light border-info font-monospace" placeholder="123456" pattern="[0-9]{6}" maxlength="6" inputmode="numeric" required autocomplete="one-time-code">
                        </div>
                        {% endif %}
                    </div>
                    <div class="modal-footer border-secondary">
                        <button type="button" class="btn btn-sm btn-secondary" data-bs-dismiss="modal">Annuler</button>
                        <button type="submit" class="btn btn-sm btn-danger fw-bold">
                            <i class="bi bi-trash3-fill me-1"></i> Confirmer la suppression
                        </button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Horloge serveur temps réel
        const serverStart = new Date("{{ server_time_iso }}").getTime();
        const clientStart = Date.now();
        function updateClock() {
            const now = new Date(serverStart + (Date.now() - clientStart));
            const el = document.getElementById('liveClock');
            if (el) el.textContent = now.toLocaleTimeString('fr-FR');
        }
        setInterval(updateClock, 1000);
        updateClock();

        // Persistance de l'onglet actif
        document.addEventListener("DOMContentLoaded", function() {
            const savedTab = localStorage.getItem("echo_user_active_tab");
            if (savedTab) {
                const tabTrigger = document.querySelector(`button[data-bs-target="${savedTab}"]`);
                if (tabTrigger) new bootstrap.Tab(tabTrigger).show();
            }
            document.querySelectorAll('#userTabs button[data-bs-toggle="pill"]').forEach(btn => {
                btn.addEventListener('shown.bs.tab', e => {
                    const target = e.target.getAttribute('data-bs-target');
                    localStorage.setItem("echo_user_active_tab", target);
                    if (target === '#tab-sessions') fetchUserSessions();
                });
            });
        });

        // Formatage compte à rebours
        function formatCountdown(sec) {
            if (sec === null || sec === undefined) return "♾️ Éternelle";
            if (sec <= 0) return "⛔ Expirée";
            const d = Math.floor(sec / 86400);
            const h = Math.floor((sec % 86400) / 3600);
            const m = Math.floor((sec % 3600) / 60);
            const s = Math.floor(sec % 60);
            const pad = n => String(n).padStart(2, '0');
            return d > 0 ? `${d}j ${pad(h)}h ${pad(m)}m ${pad(s)}s` : `${pad(h)}h ${pad(m)}m ${pad(s)}s`;
        }

        let currentSessionExpiresAt = {{ stats.current_session.expires_at if stats.current_session and stats.current_session.expires_at is not none else 'null' }};
        let currentSessionRemaining = {{ stats.current_session.time_remaining_sec if stats.current_session and stats.current_session.time_remaining_sec is not none else 'null' }};

        function renderKpiCountdown() {
            const el = document.getElementById('kpiAuthCountdown');
            if (!el) return;
            if (currentSessionExpiresAt !== null && currentSessionExpiresAt < 32503680000) {
                currentSessionRemaining = Math.max(0, currentSessionExpiresAt - Math.floor(Date.now() / 1000));
            }
            el.textContent = formatCountdown(currentSessionRemaining);
            if (currentSessionRemaining === null) {
                el.className = "stat-val font-monospace text-success";
            } else if (currentSessionRemaining <= 300) {
                el.className = "stat-val font-monospace text-danger";
            } else if (currentSessionRemaining <= 3600) {
                el.className = "stat-val font-monospace text-warning";
            } else {
                el.className = "stat-val font-monospace text-light";
            }
        }
        renderKpiCountdown();

        // Mise à jour chaque seconde du compte à rebours KPI + Table des sessions
        setInterval(() => {
            renderKpiCountdown();
            updateExportCountdown();
            const nowSec = Math.floor(Date.now() / 1000);
            document.querySelectorAll('.session-countdown').forEach(span => {
                const expRaw = span.getAttribute('data-expires-at');
                if (!expRaw || expRaw === 'null') return;
                const exp = parseInt(expRaw, 10);
                if (!isNaN(exp)) {
                    const diff = Math.max(0, exp - nowSec);
                    span.textContent = formatCountdown(diff);
                }
            });
        }, 1000);

        // Validation temps réel de la politique de mot de passe
        const pwdMinLen = {{ auth_settings.pwd_min_length }};
        const reqUpper = {{ 'true' if auth_settings.pwd_require_upper else 'false' }};
        const reqLower = {{ 'true' if auth_settings.pwd_require_lower else 'false' }};
        const reqDigit = {{ 'true' if auth_settings.pwd_require_digit else 'false' }};
        const reqSpec  = {{ 'true' if auth_settings.pwd_require_special else 'false' }};

        function setRuleState(id, ok, text) {
            const el = document.getElementById(id);
            if (!el) return;
            el.className = 'pwd-rule ' + (ok ? 'valid' : 'invalid');
            el.textContent = (ok ? '✅ ' : '❌ ') + text;
        }

        function checkPasswordRules() {
            const pwd = (document.getElementById('newPasswordInput')?.value) || '';
            const conf = (document.getElementById('confirmPasswordInput')?.value) || '';
            setRuleState('rule-len', pwd.length >= pwdMinLen, `Au moins ${pwdMinLen} caractères`);
            if (reqUpper) setRuleState('rule-upper', /[A-Z]/.test(pwd), 'Au moins une lettre majuscule (A-Z)');
            if (reqLower) setRuleState('rule-lower', /[a-z]/.test(pwd), 'Au moins une lettre minuscule (a-z)');
            if (reqDigit) setRuleState('rule-digit', /[0-9]/.test(pwd), 'Au moins un chiffre (0-9)');
            if (reqSpec)  setRuleState('rule-spec', /[^A-Za-z0-9]/.test(pwd), 'Au moins un caractère spécial (!@#$...)');
            setRuleState('rule-match', pwd.length > 0 && pwd === conf, 'Les deux mots de passe correspondent');
        }
        document.getElementById('newPasswordInput')?.addEventListener('input', checkPasswordRules);
        document.getElementById('confirmPasswordInput')?.addEventListener('input', checkPasswordRules);

        // Chargement et affichage des sessions SSO de l'utilisateur
        function escapeHtml(s) {
            return String(s || '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
        }

        const geoCache = {};
        async function fetchGeoIp(ip) {
            if (!ip || ip === '127.0.0.1' || ip.startsWith('192.168.') || ip.startsWith('10.') || ip.startsWith('172.')) return "Réseau Local";
            if (geoCache[ip]) return geoCache[ip];
            try {
                const res = await fetch(`http://ip-api.com/json/${ip}?fields=status,country,city`);
                const data = await res.json();
                if (data.status === 'success') {
                    geoCache[ip] = `${data.city || ''}, ${data.country || ''}`.replace(/^, /, '');
                    return geoCache[ip];
                }
            } catch (e) {}
            return "Inconnu";
        }

        function fetchUserSessions() {
            fetch('/api/me/sessions')
                .then(r => {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.json();
                })
                .then(data => {
                    const tbody = document.getElementById('userSessionsTableBody');
                    const badge = document.getElementById('navSessionsBadge');
                    if (!tbody) return;
                    const list = data.sessions || [];
                    if (badge) badge.textContent = list.length;
                    if (list.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">Aucune session active trouvée.</td></tr>';
                        return;
                    }
                    tbody.innerHTML = list.map((s, idx) => {
                        const isCur = !!s.is_current;
                        const trustBadge = s.is_trusted
                            ? '<span class="badge bg-success bg-opacity-25 text-success border border-success">🛡️ Confiance</span>'
                            : '<span class="badge bg-warning bg-opacity-25 text-warning border border-warning">⏳ Standard</span>';
                        const curBadge = isCur
                            ? '<span class="badge bg-primary ms-1">🟢 Cet appareil</span>'
                            : '';
                        const expAttr = (s.expires_at !== null && s.expires_at !== undefined && s.expires_at < 32503680000) ? s.expires_at : 'null';
                        const remText = formatCountdown(s.time_remaining_sec);
                        const sidShort = escapeHtml((s.session_id || '').substring(0, 10)) + '…';
                        return `
                            <tr class="${isCur ? 'table-active' : ''}">
                                <td class="ps-3">
                                    <div class="font-monospace small text-info">${sidShort}</div>
                                    <div class="mt-1">${trustBadge}${curBadge}</div>
                                </td>
                                <td>
                                    <div class="fw-semibold">${escapeHtml(s.device || 'Appareil')} &bull; ${escapeHtml(s.os || 'OS')}</div>
                                    <div class="small text-muted">${escapeHtml(s.browser || 'Navigateur')}</div>
                                </td>
                                <td>
                                    <div class="font-monospace small text-warning">${escapeHtml(s.ip_address || 'N/A')}</div>
                                    <div class="small text-muted" id="geo-u-${idx}">Localisation...</div>
                                </td>
                                <td>
                                    <div class="small text-muted">Créée : ${escapeHtml(s.created_dt)}</div>
                                    <div class="small">Exp : <span class="text-light">${escapeHtml(s.expires_dt)}</span></div>
                                    <div class="small font-monospace text-info session-countdown" data-expires-at="${expAttr}">${remText}</div>
                                </td>
                                <td class="text-end pe-3">
                                    <button class="btn btn-sm ${isCur ? 'btn-danger' : 'btn-outline-danger'}" onclick="revokeSession('${escapeHtml(s.session_id)}', ${isCur})">
                                        <i class="bi bi-power me-1"></i>${isCur ? 'Me déconnecter' : 'Révoquer'}
                                    </button>
                                </td>
                            </tr>
                        `;
                    }).join('');
                    list.forEach(async (s, idx) => {
                        const loc = await fetchGeoIp(s.ip_address);
                        const el = document.getElementById(`geo-u-${idx}`);
                        if (el) el.innerHTML = `<i class="bi bi-geo-alt-fill text-danger me-1"></i>${escapeHtml(loc)}`;
                    });
                })
                .catch(err => {
                    const tbody = document.getElementById('userSessionsTableBody');
                    if (tbody) {
                        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-4">Erreur lors du chargement des sessions (${escapeHtml(err.message)}).</td></tr>`;
                    }
                });
        }

        function revokeSession(sessionId, isCurrent) {
            const msg = isCurrent
                ? "Révoquer votre session actuelle et vous déconnecter immédiatement ?"
                : "Révoquer cette connexion à distance ?";
            if (!confirm(msg)) return;
            fetch('/action/session/revoke', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: sessionId})
            })
            .then(r => r.json())
            .then(res => {
                if (res.revoked_current) {
                    window.location.href = '/logout';
                } else {
                    fetchUserSessions();
                    refreshStats();
                }
            });
        }

        function revokeOtherSessions() {
            if (!confirm("Déconnecter immédiatement tous vos autres appareils connectés ?")) return;
            fetch('/action/session/revoke_others', {method: 'POST'})
                .then(r => r.json())
                .then(() => {
                    fetchUserSessions();
                    refreshStats();
                });
        }

        function refreshStats() {
            fetch('/api/me/stats')
                .then(r => r.json())
                .then(st => {
                    const pSize = document.getElementById('kpiProfileSize');
                    if (pSize) pSize.textContent = st.profile_size_fmt;
                    const pBrk = document.getElementById('kpiProfileBreakdown');
                    if (pBrk) pBrk.innerHTML = `Chats: ${st.chats_size_fmt} &bull; Fichiers: ${st.files_size_fmt} &bull; Skills: ${st.skills_size_fmt} &bull; Codex: ${st.codex_size_fmt}`;
                    const cCnt = document.getElementById('kpiChatCount');
                    if (cCnt) cCnt.textContent = st.chat_count;
                    const sCnt = document.getElementById('kpiSessionDbCount');
                    if (sCnt) sCnt.textContent = `${st.session_db_count} base(s) de session active(s) (session.db)`;
                    const qTot = document.getElementById('kpiQdrantTotal');
                    if (qTot) qTot.innerHTML = `${st.qdrant_total_count} <small class="fs-6 text-muted">pts</small>`;
                    const qDet = document.getElementById('kpiQdrantDetail');
                    if (qDet) qDet.innerHTML = `Souvenirs LTM: ${st.qdrant_meta_count} &bull; Segments RAG: ${st.qdrant_rag_count}`;
                    const navB = document.getElementById('navSessionsBadge');
                    if (navB) navB.textContent = st.active_sessions_count;
                    if (st.current_session) {
                        currentSessionExpiresAt = st.current_session.expires_at;
                        currentSessionRemaining = st.current_session.time_remaining_sec;
                        renderKpiCountdown();
                    }
                })
                .catch(() => {});
        }

        // Pilotage Asynchrone de l'Export (.tar.xz) avec Barre de Progression
        let exportPollTimer = null;
        let exportExpiresAt = null;
        let exportIsReady = false;

        function updateExportCountdown() {
            if (!exportIsReady || !exportExpiresAt) return;
            const nowSec = Math.floor(Date.now() / 1000);
            const diff = Math.max(0, exportExpiresAt - nowSec);
            const valEl = document.getElementById('exportCountdownVal');
            if (valEl) valEl.textContent = formatCountdown(diff);
            if (diff <= 0) {
                exportIsReady = false;
                exportExpiresAt = null;
                const readyCard = document.getElementById('exportReadyCard');
                const btnContainer = document.getElementById('exportActionContainer');
                if (readyCard) readyCard.classList.add('d-none');
                if (btnContainer) btnContainer.classList.remove('d-none');
                alert("Le délai de téléchargement de l'archive (15 minutes) a expiré. Vous pouvez générer un nouvel export.");
                pollExportStatus();
            }
        }

        function renderExportState(st) {
            const btnContainer = document.getElementById('exportActionContainer');
            const progressCard = document.getElementById('exportProgressCard');
            const readyCard = document.getElementById('exportReadyCard');
            const pBar = document.getElementById('exportProgressBar');
            const pPct = document.getElementById('exportProgressPercent');
            const pStep = document.getElementById('exportProgressStep');
            const readySize = document.getElementById('exportReadySize');
            const readyInfo = document.getElementById('exportReadyInfo');

            if (!btnContainer || !progressCard || !readyCard) return;

            if (st.is_running) {
                exportIsReady = false;
                exportExpiresAt = null;
                btnContainer.classList.add('d-none');
                readyCard.classList.add('d-none');
                progressCard.classList.remove('d-none');

                const pct = Math.max(2, Math.min(99, Math.round(st.percent || 0)));
                if (pBar) pBar.style.width = pct + '%';
                if (pPct) pPct.textContent = pct + '%';
                if (pStep) pStep.textContent = st.step || 'Exportation en cours...';
            } else if (st.ready) {
                exportIsReady = true;
                if (st.expires_at) {
                    exportExpiresAt = parseInt(st.expires_at, 10);
                } else if (st.expires_in_sec) {
                    exportExpiresAt = Math.floor(Date.now() / 1000) + parseInt(st.expires_in_sec, 10);
                }
                btnContainer.classList.add('d-none');
                progressCard.classList.add('d-none');
                readyCard.classList.remove('d-none');

                if (readySize) readySize.textContent = st.size_fmt || 'archive';
                if (readyInfo) {
                    readyInfo.textContent = `${st.filename || 'Archive'} (${st.size_fmt || ''})`;
                }
                updateExportCountdown();
            } else {
                exportIsReady = false;
                exportExpiresAt = null;
                progressCard.classList.add('d-none');
                readyCard.classList.add('d-none');
                btnContainer.classList.remove('d-none');
                if (st.error) {
                    alert("Erreur lors de l'export : " + st.error);
                }
            }
        }

        function pollExportStatus() {
            clearTimeout(exportPollTimer);
            fetch('/api/me/export/status')
                .then(r => r.json())
                .then(st => {
                    renderExportState(st);
                    if (st.is_running) {
                        exportPollTimer = setTimeout(pollExportStatus, 1500);
                    } else if (st.ready) {
                        exportPollTimer = setTimeout(pollExportStatus, 15000);
                    }
                })
                .catch(() => {});
        }

        function startExport() {
            clearTimeout(exportPollTimer);
            const btn = document.getElementById('btnStartExport');
            if (btn) btn.classList.add('disabled');

            fetch('/api/me/export/start', {method: 'POST'})
                .then(r => r.json())
                .then(st => {
                    renderExportState(st);
                    exportPollTimer = setTimeout(pollExportStatus, 1500);
                })
                .catch(err => {
                    alert("Impossible de démarrer l'export : " + err.message);
                })
                .finally(() => {
                    if (btn) btn.classList.remove('disabled');
                });
        }

        function cancelExport() {
            if (!confirm("Annuler l'export ou supprimer l'archive en attente ?")) return;
            clearTimeout(exportPollTimer);
            fetch('/action/account/export/cancel', {method: 'POST'})
                .then(r => r.json())
                .then(() => {
                    pollExportStatus();
                });
        }

        function onDownloadStarted() {
            setTimeout(pollExportStatus, 5000);
        }

        fetchUserSessions();
        pollExportStatus();
        setInterval(refreshStats, 30000);
    </script>
</body>
</html>
"""
