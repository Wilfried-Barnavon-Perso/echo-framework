---
domain_id: web_search_and_browsing
version: 6.24.0.1
updated_at: 2026-11-26
name: Recherche Web Souveraine, Navigateur Autonome & Géolocalisation Maps
description: Escalade de recherche Web (Réponses instantanées, Méta-moteur de recherche, Deep Research), navigation interactive via le Navigateur Autonome et ancrage cartographique Maps.
tools: [search_instant_answer, search_web, delegate_deep_research, delegate_web_browsing, analyze_web_page, archive_web_page, web_browse_reset, get_browser_frames_history, retain_browser_frame, open_browser_ui, search_maps]
keywords: [web, recherche, search, searxng, moteurs, engines, navigateur, browser, browserview, browserviews, capture, frame, clip, mp4, http_status, url, page, maps, carte, géolocalisation, deep_research, sub_sid, cohort_id, allowed_tools, actualité, science, scholar, arxiv, pubmed, openalex, cve, nvd, huggingface, cinéma, movies, imdb, senscritique, météo, weather, livres, annas]
---

# MANUEL OPÉRATIONNEL : RECHERCHE WEB, NAVIGATEUR AUTONOME & MAPS (`web_search_and_browsing`)

## 1. HIÉRARCHIE D'ESCALADE DE L'INVESTIGATION WEB

1. **Niveau 1 — Définition / Fait Encyclopédique Statique (`search_instant_answer(query)`)** :
   - `query: str` (**REQUIS**). Interroge le moteur de réponses instantanées pour des définitions de concepts, biographies ou dates établies (**proscrit pour l'actualité**).
   - **Enchaînement** : Si `status="no_result"`, Le Modèle DOIT basculer sur `search_web`.
2. **Niveau 2 — Recherche Web Rapide par Extraits (`search_web(query, categories=None, time_range=None, language="fr-FR", safesearch=1, engines=None)`)** :
   - `query: str` (**REQUIS**). Interroge le Méta-moteur de recherche et retourne jusqu'à 8 extraits courts (snippets), URLs ainsi que les blocs structurés `answers` et `infoboxes` lorsqu'ils sont disponibles.
   - `categories` : `'general'`, `'it'`, `'science'`, `'images'`, `'videos'`, `'social media'`, `'news'`, `'music'`, `'map'`, `'files'`, `'movies'`, `'weather'`.
   - `time_range` : `'day'`, `'week'`, `'month'`, `'year'`. Le Modèle **DOIT TOUJOURS** renseigner `time_range` dès que la requête porte sur l'actualité ou des faits récents.
   - `language` : `'fr-FR'` (défaut), `'en-US'` ou `'all'` ; `safesearch` : `1` (défaut) ; `engines` : moteurs cibles optionnels séparés par des virgules.
   - **RÈGLE ANTI-SPAM** : Il est **ABSOLUMENT INTERDIT** d'exécuter plus de 2 appels simultanés à `search_web` dans un même tour.
   - **Vérification du contenu complet** : Si les extraits sont insuffisants ou nécessitent lecture intégrale d'une URL identifiée, Le Modèle DOIT utiliser `analyze_web_page(url, query)` ou `delegate_deep_research`.
3. **Niveau 3 — Recherche Profonde Multi-Sources (`delegate_deep_research(query, engines=None, target_model_key="MODEL_PROMETHEUS", sub_sid=None, cohort_id=None, allowed_tools=None)`)** :
   - **Contrainte d'environnement** : Autorisé pour l'Orchestrateur principal et les supérieurs hiérarchiques `can_delegate=True` (`Stratège` / `Capitaine` avec `depth < ECHO_MAX_PHALANX_DEPTH = 3`, propageant automatiquement `parent_sid`, `group_id` et `depth + 1`). **ABSOLUMENT INTERDIT** depuis un `Hoplite` feuille (`can_delegate=False`).
   - `query: str` (**REQUIS**). Délègue une investigation complexe à un Sous-Agent autonome multi-tours (`target_model_key`: `"MODEL_ZEPHYR" | "MODEL_APOLLO" | "MODEL_PROMETHEUS" | "MODEL_ATHENA"`, défaut `"MODEL_PROMETHEUS"` ; `engines` optionnel ex: `'google scholar,semantic scholar,wikipedia'` ; passe autonome bornée à **15 tours d'outils / 300s**) qui croise les requêtes, ouvre les pages via le Navigateur Autonome et synthétise un rapport sourcé.
   - `sub_sid` (`Optional[str]`, défaut `None`) : Identifiant explicite optionnel pour la session `thread_deepresearch_<hex8>` ou sa reprise. Une session terminée se clôt via `control_subagents(action="close", target_ids=[sub_sid])`.
   - `cohort_id` (`Optional[str]`, défaut `None`) : Rattache la session à une Cohorte légère (`phx_<id>`) lorsqu'elle est invoquée hors d'un groupe hiérarchique parent.
   - `allowed_tools` (`Optional[List[str]]`, défaut `None`) : Surcharge ou patch (`["*"]`, `["+outil", "-outil"]`, liste explicite) appliqué à la liste par défaut (`search_web`, `search_instant_answer`, `search_maps`, `wait_timer`, `delegate_web_browsing`).
4. **Niveau 4 — Interaction Web Complexe (`delegate_web_browsing`)** :
   - **Contrainte d'environnement** : Autorisé pour l'Orchestrateur principal, les supérieurs hiérarchiques `can_delegate=True` (`Stratège` / `Capitaine` avec `depth < ECHO_MAX_PHALANX_DEPTH = 3`) et les Sous-Agents disposant de l'outil dans leur pool (`allowed_tools`).
   - Déploie le Navigateur Autonome doté de vision multimodale et de contrôle du DOM pour naviguer sur des applications web dynamiques, cliquer, remplir des formulaires ou paginer.

### 1.1 Référentiel des identifiants exacts `engines` (par catégorie)
Lorsque `categories` suffit, omettre `engines` pour interroger tout le pool actif de la catégorie. Pour cibler un sous-ensemble via `engines` (séparés par des virgules, espaces internes conservés) :
- `general` : `google`, `google cse`, `bing`, `duckduckgo`, `qwant`, `yahoo`, `yep`, `mwmbl`, `crowdview`, `wikipedia`, `wikidata`, `wolframalpha`, `ddg definitions`, `encyclosearch`, `wikibooks`, `wikisource`, `wikiquote`, `wikiversity`, `wikivoyage`, `wikispecies`, `currency`.
- `it` : `github`, `gitlab`, `codeberg`, `bitbucket`, `sourcehut`, `gitea.com`, `stackoverflow`, `superuser`, `askubuntu`, `discuss.python`, `hackernews`, `lobste.rs`, `mdn`, `microsoft learn`, `huggingface`, `huggingface datasets`, `huggingface spaces`, `ollama`, `pypi`, `npm`, `crates.io`, `lib.rs`, `pkg.go.dev`, `nuget`, `packagist`, `rubygems`, `docker hub`, `national vulnerability database`, `arch linux wiki`, `gentoo`, `nixos wiki`, `alpine linux packages`, `hoogle`, `mankier`, `fdroid`, `apk mirror`, `apple app store`, `google play apps`.
- `science` : `arxiv`, `pubmed`, `google scholar`, `semantic scholar`, `openalex`, `crossref`, `europepmc`, `openairedatasets`, `openairepublications`, `pdbe`, `wikispecies`.
- `news` : `google news`, `bing news`, `duckduckgo news`, `qwant news`, `brave.news`, `reuters`, `wikinews`, `tagesschau`, `ansa`, `il post`, `tusksearch news`, `fireball news`, `naver news`.
- `images` : `google images`, `bing images`, `duckduckgo images`, `qwant images`, `brave.images`, `google cse images`, `openverse`, `pixabay images`, `artic`, `pinterest`, `wikicommons.images`, `unsplash`, `pexels`, `500px`, `picjumbo`, `stocksnap`, `shopify stock`, `1x`.
- `videos` : `youtube`, `google videos`, `bing videos`, `duckduckgo videos`, `qwant videos`, `brave.videos`, `dailymotion`, `vimeo`, `ina`, `media.ccc.de`, `pixabay videos`, `odysee`, `sepiasearch`, `wikicommons.videos`.
- `social media` : `lemmy posts`, `lemmy communities`, `lemmy comments`, `lemmy users`, `mastodon users`, `mastodon hashtags`, `tootfinder` *(pour Reddit / X, utiliser `general` avec `site:reddit.com` ou `site:x.com`)*.
- `music` : `youtube`, `deezer`, `soundcloud`, `bandcamp`, `genius`, `mixcloud`, `radio browser`, `wikicommons.audio`.
- `files` : `annas archive`, `findfiles`, `wikicommons.files`.
- `map` : `openstreetmap`, `photon`, `apple maps`.
- `movies` : `imdb`, `tmdb`, `rottentomatoes`, `senscritique`.
- `weather` : `wttr.in`, `openmeteo`.

---

## 2. LE NAVIGATEUR AUTONOME

### 2.1 Lecture Sémantique & Archivage Direct d'une URL
- `analyze_web_page(url, query)` (**À PRIVILÉGIER sur une URL connue**) :
  - Ouvre `url`, extrait l'intégralité du texte de la page et délègue son analyse à un Sous-Agent (`MODEL_APOLLO`) pour répondre précisément à la directive `query` sans saturer la Mémoire Vectorielle de la session.
- `archive_web_page(url)` :
  - Extrait le texte intégral d'une page web volumineuse, l'écrit dans `{domain}_{hex6}_archive.md` et l'enregistre au statut `pending_ingestion` (vectorisé automatiquement dans la **Mémoire Vectorielle de la session** au tour suivant, interrogeable via `search_sessions_context`).

### 2.2 Navigation Interactive Multi-Étapes (`delegate_web_browsing`)
- **Concurrence globale** : Le Système limite les instances navigateur actives simultanément à **`ECHO_MAX_CONCURRENT_BROWSERS = 2`**.
- `task_objective` : Mission complète confiée au Navigateur Autonome (DOIT inclure le contexte général et spatio-temporel utile).
- `start_url` : URL initiale optionnelle.
- `sub_sid` : Identifiant de session de navigation optionnel (`thread_web_*`). Permet de reprendre une navigation existante après un point de contrôle (`status="suspended_checkpoint"` au bout de **8 tours d'actions / 120s**) soit en rappelant `delegate_web_browsing(task_objective=..., sub_sid=...)`, soit via `control_subagents(action="resume", target_ids=[sub_sid])`. Les métadonnées de retour incluent la télémétrie HTTP (`final_url`, `http_status`, `http_status_text`, `navigation_statuses`), et Le Sous-Agent est informé des codes HTTP `>= 400`.
- `cohort_id` (`Optional[str]`, défaut `None`) : Rattache la session de navigation à une Cohorte légère (`phx_<id>`) lorsqu'elle est invoquée hors d'un groupe hiérarchique parent.
- `target_model_key` : `"MODEL_ZEPHYR"`, `"MODEL_APOLLO"` (défaut), `"MODEL_PROMETHEUS"` ou `"MODEL_ATHENA"`.
- **Administration du Navigateur Autonome** :
  - `get_browser_frames_history(depth=10)` : Opère en **lecture seule non destructive** et restitue l'historique des dernières captures d'écran présentes sur disque (`file_id`, `date`, `frame`, `session_id`, `url`, `http_status`, `action`, `status`, `retained`, et `has_clip` indiquant la présence d'un clip vidéo MP4 d'action rejouable et téléchargeable dans l'onglet Navigateur Web).
  - `retain_browser_frame(file_id, retain=True)` : Marque une capture de navigation (`resource_type="browserview"`, stockée dans `browserviews/`) au statut `"retained"` pour la préserver (ainsi que son clip vidéo MP4 d'action) de la purge automatique en fin de session (ou la repasse en `"transient"` si `retain=False`). Les clips `"transient"` sont soumis à un plafond FIFO par conversation (`50 clips / 60 Mo`, sans supprimer les captures HD), les captures `"transient"` bénéficient d'un délai de grâce de **`1800s` (30 min)** avant purge en fin de session navigateur (`web_browse_reset` / `control_subagents(action="close", target_ids=[sub_sid])` / expiration), et le clonage/migration de session ne transfère que les captures `"retained"`.
  - `open_browser_ui(frame_index=None)` : Ouvre l'onglet natif **Navigateur Web** dans l'interface de L'Utilisateur avec suivi temps réel continu (ou positionné sur l'index de capture indiqué). **ABSOLUMENT INTERDIT** depuis un Sous-Agent headless (`DELEGATE_AGENT_BLACKLIST`).
  - `web_browse_reset()` : Réinitialise immédiatement la session active du Navigateur Autonome.
  - Fermeture et purge d'une session `thread_web_*` : Le Modèle DOIT appeler **`control_subagents(action="close", target_ids=[sub_sid])`**.

---

## 3. GÉOLOCALISATION & AFFICHAGE DE CARTES (`search_maps`)
- `search_maps(query, latitude=None, longitude=None, print_map=True)` :
  - Exécuté sous `MODEL_ZEPHYR` (`thinking="minimal"`). Recherche des données géo-spatiales vérifiées (lieux, adresses, horaires, itinéraires, avis) et affiche une carte interactive (si `print_map=True`).
  - **Contrainte Sous-Agent Headless** : Tout Sous-Agent headless appelant `search_maps` **DOIT IMPÉRATIVEMENT** passer `print_map=False`.
  - `latitude` / `longitude` : Coordonnées décimales optionnelles (issues de `<artifact id="AEC_localisation">`) pour centrer la recherche. Elles **DOIVENT** être fournies conjointement (les deux ou aucune).
