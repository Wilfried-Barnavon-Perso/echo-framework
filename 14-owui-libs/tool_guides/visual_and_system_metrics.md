---
domain_id: visual_and_system_metrics
version: 5.231.52.1
updated_at: 2026-11-02
name: Moteur de Rendu Visuel Interactif (ECHO Visual) & Jauge de Contexte
description: Génération asynchrone d'interfaces et visualisations interactives (17 moteurs : Mermaid, ECharts, Markmap, Leaflet, etc.) et surveillance de la charge du contexte (get_context_load).
tools: [generate_rich_visualization, get_context_load]
keywords: [visualisation, graphique, echarts, mermaid, mindmap, markmap, leaflet, timeline, gantt, cytoscape, contexte, tokens, get_context_load]
---

# MANUEL OPÉRATIONNEL : MOTEUR VISUEL & JAUGE DE CONTEXTE (`visual_and_system_metrics`)

## 1. MOTEUR DE RENDU VISUEL UNIVERSEL (`generate_rich_visualization`)
- **Contrainte d'environnement** : Réservé à l'Orchestrateur principal (**interdit depuis un sous-agent headless**).
- **Spécifications des Paramètres** :
  - `intention` : Objectif précis du rendu visuel (ex: `"Comparer l'évolution trimestrielle du CA et de la marge sous forme de graphique mixte barres/lignes"`).
  - `donnees_contextuelles` : **Intégralité des données brutes** (chiffres, séries, nœuds, coordonnées, chronologie) nécessaires à la construction du visuel. Le sous-agent visuel n'ayant pas accès à l'historique de la conversation, aucune donnée ne doit être sous-entendue.
  - `moteur` (Optionnel — laisser `None` pour sélection automatique ou imposer l'un des 17 moteurs) :
    - **Diagrammes & Cartes Mentales** : `"mermaid"`, `"markmap"`, `"bpmn"`, `"gantt"`, `"wavedrom"`.
    - **Data Visualization & Graphes** : `"echarts"`, `"vega"`, `"cytoscape"`, `"timeline"`.
    - **Cartographie** : `"leaflet"` (marqueurs `{"center": [lat, lng], "zoom": int, "markers": [{"lat": float, "lng": float, "popup": "html"}]}`).
    - **Sciences, 3D & Dessin** : `"aframe"`, `"svg"`, `"sketch"`, `"chem"`, `"science"`, `"bio"`, `"astro"`.
  - `niveau_cognitif` : `"MODEL_ZEPHYR"`, `"MODEL_APOLLO"` (défaut) ou `"MODEL_PROMETHEUS"`.

---

## 2. SURVEILLANCE DE LA SATURATION DU CONTEXTE (`get_context_load`)
- Retourne un objet JSON contenant `context_load_percent`, `used_tokens` (`prompt`, `candidates`) et `status` (`"SAFE"`, `"WARNING"` ou `"CRITICAL"`).
- **Règle d'Escalade Cognitive** : Si `status` atteint `"CRITICAL"`, privilégier `MODEL_PROMETHEUS` et déléguer les opérations lourdes aux sous-agents (`delegate_to_agent`) pour préserver la fenêtre d'attention.
