---
domain_id: memory_and_rag
version: 5.231.52.1
updated_at: 2026-11-02
name: Mémoire Vectorielle Long-Terme (Méta-Artéfacts) & Mémoire Vectorielle de Session
description: Gouvernance de la mémoire persistante inter-sessions (PACP/PRAC) et de la Mémoire Vectorielle de la session (documents ingérés et historique conversationnel).
tools: [update_meta_artifact, search_meta_artifacts, delete_meta_artifact_item, save_session_context, delete_session_context_source, search_sessions_context]
keywords: [mémoire, rag, meta_artifact, pacp, prac, profil_alignement, hypotheses_apprentissage, smart_context, session, souvenir, importance, certainty_level]
---

# MANUEL OPÉRATIONNEL : MÉMOIRE VECTORIELLE LONG-TERME & DE SESSION (`memory_and_rag`)

## 1. ARCHITECTURE & MATRICE DE DÉCISION
Le système mémoriel d'ECHO est scindé en deux espaces vectoriels isolés :
1. **Méta-Artéfacts Long-Terme (Inter-Sessions)** : Stockent les invariants cognitifs de l'Utilisateur (`PACP`) et les heuristiques d'apprentissage du Modèle (`PRAC`).
   - Création / Mise à jour : `update_meta_artifact`
   - Recherche sémantique ou Lecture globale : `search_meta_artifacts`
   - Suppression chirurgicale : `delete_meta_artifact_item`
2. **Mémoire Vectorielle de la Session (Intra/Inter-Sessions)** : Stocke les documents ingérés (`AEC_smart_context`), les captures d'outils et les échanges conversationnels vectorisés.
   - Exploration sémantique ou Cartographie d'index : `search_sessions_context`
   - Sauvegarde explicite d'un fait/extrait dans la session : `save_session_context`
   - Purge d'une source de la session : `delete_session_context_source`

---

## 2. PROTOCOLE DES MÉTA-ARTÉFACTS LONG-TERME (`PACP` / `PRAC`)

### 2.1 Taxonomie Stricte des Catégories (`artifact_name`)
Le paramètre `artifact_name` n'accepte que deux valeurs littérales :
- `"Profil d'Alignement"` (**PACP**) : Préférences explicites ou inférées de l'Utilisateur, style cognitif, contraintes métier récurrentes, stack technique personnelle, attentes de formatage.
- `"Hypothèses d'Apprentissage"` (**PRAC**) : Rétrospectives opérationnelles du Modèle, stratégies d'outils optimales, contournements d'erreurs découverts, patterns validés.

### 2.2 Échelle Déterministe de Pondération (`importance`)
Dans `update_meta_artifact(artifact_name, fact, importance)`, le paramètre `importance` (`Literal["1", "2", "3", "4", "5"]`) est **REQUIS** :
- `"1"` (**Trivial**) : 1 occurrence isolée observée, indice faible non confirmé.
- `"2"` (**Mineur**) : 2 occurrences concordantes rapprochées, préférence secondaire.
- `"3"` (**Utile**) : 3 occurrences concordantes, fait vérifié ou habitude opérationnelle établie.
- `"4"` (**Majeur**) : 3 occurrences concordantes structurantes, préférence forte ou architecture clé.
- `"5"` (**Axiome**) : Injonction explicite de l'Utilisateur ou invariant stratégique critique validé.

### 2.3 Spécifications des Fonctions Méta-Artéfacts
- `update_meta_artifact` :
  - `fact` : Énoncé factuel, dense et autonome (sans pronoms ambigus ni références temporelles relatives).
  - **Retour** : Génère et retourne le `memory_id` unique (indispensable pour suppression ultérieure).
- `search_meta_artifacts` :
  - `artifact_name` : `"Profil d'Alignement"`, `"Hypothèses d'Apprentissage"`, ou `None` (les deux simultanément).
  - **Mode Recherche Ciblée (`query` renseigné)** : Extraction sémantique pondérée par niveau d'importance.
  - **Mode Lecture Globale (`query=None` ou omis)** : Extraction chronologique des `limit` derniers faits mémorisés avec leurs `memory_id` (**REQUIS** avant suppression).
  - `start_date` / `end_date` : Bornes temporelles optionnelles au format ISO 8601 (`"YYYY-MM-DD"`).
- `delete_meta_artifact_item` :
  - Supprime définitivement un souvenir à partir de son `memory_id` exact (obtenu via `update_meta_artifact` ou `search_meta_artifacts`).

---

## 3. PROTOCOLE DE LA MÉMOIRE VECTORIELLE DE SESSION (`search_sessions_context`)

### 3.1 Exploitation des Documents Ingérés (`AEC_smart_context`)
Lorsqu'un document est ingéré, la balise `<artifact id="AEC_smart_context">` fournit son `source_id` et son résumé exécutif.
- Le Modèle **NE DOIT PAS** interroger la Mémoire Vectorielle de la session si le résumé du `AEC_smart_context` suffit.
- Si une précision granulaire est requise, appeler `search_sessions_context` en filtrant sur `source_id`.

### 3.2 Spécifications de `search_sessions_context`
- `certainty_level` (**Obligatoire**) : Niveau de certitude sémantique attendu :
  - `"BROAD"` : Exploration large, concepts vagues ou reformulés.
  - `"STANDARD"` : Équilibre nominal pertinence/rappel.
  - `"STRICT"` : Recherche factuelle à haute correspondance sémantique.
  - `"EXACT"` : Recherche quasi-littérale d'un terme ou identifiant.
- `query` :
  - **Défini (`query="..."`)** : Recherche et extrait les fragments textuels complets dédupliqués.
  - **Omis (`query=None`)** : **Mode Cartographie d'Index** — retourne la liste des `source_id` indexés dans la session avec horodatage, tags et aperçu.
- `tag` :
  - Restreint la recherche aux conversations rattachées à cette étiquette.
  - **Prérequis obligatoire** : Appeler préalablement `list_ui_tags(global_scope=True)` pour identifier la chaîne anglaise exacte (`snake_case` / `kebab-case`).
- `global_search` :
  - `False` (Défaut) : Restreint la recherche à la session active.
  - `True` : Étend la recherche à **l'intégralité de l'historique inter-sessions** de l'Utilisateur (**DOIT** être activé dès que l'Utilisateur mentionne un échange antérieur).
- `sort_by_time` : Si `True`, trie les résultats du plus récent au plus ancien.

### 3.3 Persistance et Purge de Sources de Session
- `save_session_context(text, source_id)` : Remplace tout vecteur existant sous `source_id` puis vectorise `text` dans la session active.
- `delete_session_context_source(source_id)` : Purge tous les vecteurs associés à `source_id` dans la session active (un sous-agent ne peut purger que les sources qu'il a lui-même créées).
