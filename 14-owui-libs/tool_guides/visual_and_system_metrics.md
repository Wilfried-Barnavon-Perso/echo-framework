---
domain_id: visual_and_system_metrics
name: Moteur de Rendu Visuel Interactif (ECHO Visual) & Jauge de Contexte
description: Génération asynchrone d'interfaces et visualisations interactives (17 moteurs : Mermaid, ECharts, Markmap, Leaflet, etc.) et surveillance de la charge du contexte (get_context_load).
tools: [generate_rich_visualization, get_context_load]
keywords: [visualisation, graphique, echarts, mermaid, mindmap, markmap, leaflet, timeline, gantt, cytoscape, contexte, tokens, get_context_load]
---

# MANUEL OPÉRATIONNEL : MOTEUR VISUEL & JAUGE DE CONTEXTE (`visual_and_system_metrics`)

## 1. MOTEUR DE RENDU VISUEL UNIVERSEL (`generate_rich_visualization`)
`generate_rich_visualization` délègue à un sous-agent spécialisé (par défaut `MODEL_APOLLO`, avec boucle d'auto-réparation syntaxique JSON) la création d'un composant visuel interactif isolé en Data Island dans l'interface de l'Utilisateur.

### Spécifications des Paramètres
`generate_rich_visualization(intention, donnees_contextuelles, moteur=None, niveau_cognitif="MODEL_APOLLO")` :
- `intention` : Objectif précis du rendu visuel (ex: `"Comparer l'évolution trimestrielle du CA et de la marge sous forme de graphique mixte barres/lignes"`).
- `donnees_contextuelles` : **Intégralité des données brutes** (chiffres, séries, nœuds, coordonnées, chronologie) nécessaires à la construction du visuel. Le sous-agent visuel n'ayant pas accès à l'historique de la conversation, aucune donnée ne doit être sous-entendue.
- `moteur` (Optionnel — laisser `None` pour sélection automatique ou imposer l'un des 17 moteurs supportés) :
  - **Diagrammes & Cartes Mentales** : `"mermaid"` (flowcharts, séquences, architectures), `"markmap"` (mindmaps hiérarchiques interactives), `"bpmn"`, `"gantt"`, `"wavedrom"` (chronogrammes logiques).
  - **Data Visualization & Graphes** : `"echarts"` (graphiques statistiques riches v5), `"vega"` (Vega-Lite), `"cytoscape"` (graphes de réseaux complexes), `"timeline"`.
  - **Cartographie** : `"leaflet"` (carte interactive avec marqueurs `{"center": [lat, lng], "zoom": int, "markers": [{"lat": float, "lng": float, "popup": "html"}]}`).
  - **Sciences, 3D & Dessin** : `"aframe"` (scènes 3D/VR), `"svg"`, `"sketch"`, `"chem"`, `"science"`, `"bio"`, `"astro"`.
- `niveau_cognitif` : `"MODEL_ZEPHYR"`, `"MODEL_APOLLO"` (défaut) ou `"MODEL_PROMETHEUS"` (pour les modélisations très complexes).

---

## 2. SURVEILLANCE DE LA SATURATION DU CONTEXTE (`get_context_load`)
`get_context_load()` :
- Lit la télémétrie de la session active et retourne un objet JSON contenant :
  - `context_load_percent` : Pourcentage d'occupation de la fenêtre de contexte.
  - `used_tokens` & `details` (`prompt`, `candidates`).
  - `status` : `"SAFE"`, `"WARNING"` ou `"CRITICAL"`.
- **Règle d'Escalade Cognitive** : Si `status` atteint `"CRITICAL"`, il est recommandé de privilégier `MODEL_PROMETHEUS` et de déléguer les opérations lourdes aux sous-agents (`delegate_to_agent`) pour préserver la fenêtre d'attention.
