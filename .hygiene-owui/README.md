# `.hygiene-owui` — Gouvernance locale des dépendances et veille amont

Répertoire de pilotage local (100 % hors CI distante) de l'hygiène logicielle, des vulnérabilités `npm`/`pip`, de la conformité des `Dockerfile` et de la veille différentielle amont Open WebUI pour `16-docker-echo-webui`.

## Structure

- `HYGIENE_POLICY.json` : Politique déclarative (sévérités bloquantes, paliers majeurs verrouillés `pinned_majors`, exceptions CVE datées `accepted_exceptions`, transitifs dépréciés autorisés).
- `check-echo-hygiene.ps1` : Script principal d'audit et de mise à jour sécurisée (`FixSafe` avec rollback automatique).
- `check-owui-news.ps1` : Veille différentielle sur le miroir `.tmp_audit/open-webui` (filtrage des `backported_commits` de `UPSTREAM_LOCK.json`).
- `reports/` : Rapports Markdown générés localement (ignorés par Git via `.gitignore`).

## Commandes

```powershell
# Audit complet (bloquant si CVE non acceptée, deprecated non autorisé, ou dérive Dockerfile/requirements)
.\.hygiene-owui\check-echo-hygiene.ps1 -Mode Audit

# Audit + veille différentielle amont Open WebUI
.\.hygiene-owui\check-echo-hygiene.ps1 -Mode Audit -IncludeUpstream

# Correctifs SemVer non-cassants + 5 portes de validation (svelte-kit sync, vitest, i18n:check, crypto:verify, vite build) avec rollback automatique en cas d'échec
.\.hygiene-owui\check-echo-hygiene.ps1 -Mode FixSafe

# Planification hebdomadaire locale (lundis 09:00, session utilisateur)
.\.hygiene-owui\check-echo-hygiene.ps1 -RegisterScheduledTask
.\.hygiene-owui\check-echo-hygiene.ps1 -UnregisterScheduledTask
```

## Gouvernance de `HYGIENE_POLICY.json`

- Toute exception CVE dans `accepted_exceptions` doit préciser `packages`, `max_severity`, `scope`, `reason` et une date d'expiration `review_after` (`YYYY-MM-DD`).
- Une exception expirée (`Today >= review_after`) redevient automatiquement bloquante lors de l'audit.
- `pinned_majors` documente les paliers majeurs volontairement figés pour préserver la compatibilité du build ou des APIs navigateur.
