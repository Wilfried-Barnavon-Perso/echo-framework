### 🌐 Rendu Web Desktop (v5.42.4)
*   **v5.42.4** : Correctif du Browser Agent. Suppression de l'écrasement du viewport en mode tablette lors de la création de page. Le mode Desktop respecte désormais nativement la résolution PC (1280x800).

### 🛡️ Robustesse Authentification Google (v5.42.3)
*   **v5.42.3** : Détection automatique des challenges Google (403 VALIDATION_REQUIRED). Extraction du lien de validation pour permettre à l'utilisateur de débloquer son compte directement depuis le chat.
*   **v5.42.2** : Support du diagnostic profond des erreurs 403. Logging exhaustif du JSON d'erreur Google pour identifier les liens de validation (ToS, âge).

### 🛠️ Onboarding Dynamique Google One AI Pro (v5.42.0)
*   **v5.42.0** : Support de l'onboarding automatique pour les comptes **Google One AI Pro**. Implémentation du handshake `:onboardUser` (sans `cloudaicompanionProject` pour le `free-tier`) suite à l'audit du client officiel `gemini-cli`. Résolution de l'erreur "JSON inattendu" lors de la ré-authentification de nouveaux comptes.

### ⚠️ Mise à jour de Vision Heuristique (v5.41.1)
*   **v5.41.1** : Raffinement de l'IA pour la lecture de fichiers. Mise à jour de la Docstring de l'outil `read_raw_file_content` pour clarifier ses cas d'usage (bas niveau, code, chunks) et ses contre-indications (analyse sémantique), orientant ainsi l'IA vers `semantic_probe` pour les besoins conceptuels.
*   **v5.41.0** : Architecture "Persistent Session Registry". Évolution de la base SQLite pour stocker les noms de fichiers originaux.
*   **v5.40.1** : Restauration de la Richesse Opérationnelle. Ré-injection des docstrings et instructions.
*   **v5.40.0** : Architecture de Rigueur Absolue. Suppression intégrale du code défensif.
*   **v5.39.1** : Correctifs critiques post-centralisation.
*   **v5.38.0** : Architecture "Physical Indexing". Centralisation de la résolution des fichiers dans `echo_utils.py` via `resolve_upload_file_path` (Globbing par UUID). Immunisation totale contre les caractères spéciaux (apostrophes, espaces) dans les noms de fichiers. Injection d'un index technique explicite (`FILE_ID`) dans le Smart Context pour guider l'IA sans ambiguïté.
*   **v5.37.5** : Finalisation de la couche d'Authentification RO. Correction de la syntaxe URI SQLite pour Docker (format `file:///`).
*   **v5.37.4** : Correctif critique de la couche d'Authentification (DAL). Restauration de `get_google_token` dans `echo_utils.py` pour le RAG. Centralisation du nettoyage du `project_id` (suppression du préfixe `projects/`) directement dans la lib partagée pour garantir la compatibilité de tous les outils Google Cloud (Search, Expert, Smart Context).
*   **v5.35.2** : Rigueur UX du Pipe. Remplacement des messages d'erreur API textuels (yield) par des notifications natives ("Toasts") via `__event_emitter__` pour purifier l'historique de discussion. Préservation stricte du flux d'authentification par messages directs.
*   **v5.35.1** : Optimisation de l'Ergonomie HUD. Double-clic pour Reset Total.
*   **v5.34.14** : Correctif de Fluidité HUD. Neutralisation des transitions CSS.
*   **v5.34.13** : Ergonomie HUD Cockpit. Ratio dynamique et centrage intelligent.
