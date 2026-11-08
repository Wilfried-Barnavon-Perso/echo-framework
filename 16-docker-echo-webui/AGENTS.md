# 🌐 16-docker-echo-webui - Serveur & Interface Natifs ECHO V6

> **Source de Vérité Locale** : Ce document définit le rôle, la gouvernance technique, les invariants d'intégration et le cycle de maintenance du composant `16-docker-echo-webui`.

---

## 1. 🎯 Rôle & Architecture du Composant

Le répertoire `16-docker-echo-webui` héberge l'arborescence complète du serveur FastAPI et de l'interface SvelteKit native d'ECHO V6. Il remplace définitivement l'image Docker externe `ghcr.io/open-webui/open-webui`.

Cette intégration native permet :
- La compilation locale et le build optimisé du frontend SvelteKit (`src`, `static`).
- L'exécution directe et la personnalisation chirurgicale du backend FastAPI (`backend`).
- L'éradication des dépendances externes au runtime et des divergences de cycle de vie en production.

---

## 2. 🔒 Référence Amont & Veille Différentielle

La base de code de ce composant est verrouillée sur une version amont d'Open WebUI référencée dans [`UPSTREAM_LOCK.json`](file:///D:/GitHub/echo-v6/16-docker-echo-webui/UPSTREAM_LOCK.json) :
- **Dépôt amont :** `https://github.com/open-webui/open-webui.git`
- **Tag de base :** `v0.13.2`
- **Commit SHA :** `b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245`
- **Date d'extraction :** `2026-11-08`

### Protocole de Veille & Rebase
- Le sous-répertoire `.tmp_audit/open-webui` sert de miroir amont pour les audits de nouveautés et les comparaisons de code.
- Le script `check-owui-news.ps1` orchestre la veille différentielle continue vis-à-vis des nouvelles releases et commits amont.
- Toute intégration de correctif ou d'évolution amont doit être tracée et documentée en respectant la liste des chemins suivis (`tracked_paths`).

---

## 3. ⚓ Invariants Inter-Services Obligatoires

Le conteneur bâti depuis ce composant s'insère au cœur de l'architecture multi-services d'ECHO V6. Les contrats d'interface suivants sont des invariants stricts :

1. **Réseau & Service :**
   - Nom de service : `echo-open-webui`.
   - Assignation réseau interne : `172.28.0.100:8080` (réseau `echo-net`).
2. **Persistance des Données :**
   - Montage du volume persistant : `echo-webui-data:/app/backend/data`.
3. **Schéma SSoT `webui.db` :**
   - La base SQLite `/app/backend/data/webui.db` demeure la source unique de vérité partagée pour les entités clés :
     - `user` (identités utilisateurs)
     - `auth` (credentials et sessions)
     - `chat` (fils conversationnels et métadonnées)
     - `folder` (organisation hiérarchique)
     - `tag` (taxonomie et étiquetage)
     - `file` (registre des métadonnées de fichiers)
     - `config` (configurations applicatives persistantes)
     - `calendar` (gestion calendaire)
     - `automation` (définition des flux automatisés)
   - Ces tables sont interrogées et manipulées directement par les microservices satellites :
     - `20-docker-admin-manager`
     - `24-docker-echo-auth-manager`
     - `27-docker-user-manager`
     - `generalist_tools.py`

---

## 4. 🔌 Pont d'Intégration ECHO Runtime

Le backend s'interface nativement avec les briques cognitives et fonctionnelles du framework ECHO via le chargeur de runtime `echo_runtime.py` :
- **`10-owui-pipes` :** Pipeline d'orchestration cognitive et cortex LLM (`echo_orchestrator.py`).
- **`11-owui-filters` :** Chaîne de filtres d'entrée et de sortie (`new_context_filter.py`, `window_manager_filter.py`, `context_gauge_filter.py`, etc.).
- **`12-owui-tools` :** Outils métier et fonctions d'outillage agents (`agent_orchestration_tool.py`, `memory_and_rag_tool.py`, etc.).
- **`13-owui-actions` :** Déclencheurs d'actions utilisateur et intégrations d'interface.
- **`14-owui-libs` :** Bibliothèques partagées, protocoles et gestionnaires d'état (`echo_core.py`, `echo_state_manager.py`, `echo_window_manager.py`, etc.).
- **`15-owui-events` :** Gestionnaire unifié d'événements et de diffusion (`echo_events.py`).
