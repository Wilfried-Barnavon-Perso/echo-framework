# 📦 ECHO Framework v4 (Archive Historique)

Cette branche archive l'architecture historique **ECHO v4** (Kernel `4.198`), antérieure à la refonte infrastructurelle d'ECHO v5 (branches `main` et `dev`).

ECHO v4 reposait exclusivement sur un système de **System Prompt** et de fichiers externes chargés dans le contexte conversationnel d'un LLM hôte (sans infrastructure Docker, Open WebUI ni base SQLite/Qdrant).

## Contenu de l'archive

- [`echo_kernel.txt`](echo_kernel.txt) : Noyau statique ECHO v4 (`4.198`) — Méta-Principes (`MPDI`, `MPAH`, `MPCE`, `MPSI`, `MDOF`), Principes (`PGCU`, `PACP`, `PRAC`, `PRAF`, `PDSM`), Protocoles (`PTD`, `PIS`, `PIM`, `PCDM`, `PRCU`, `PECU`, `PCO`, `PCA`, `PTM`) et commandes `#...`.
- [`modules/`](modules/) : Modules d'instructions dynamiques (`*-echo-mod.txt`) :
  - [`base-proto-echo-mod.txt`](modules/base-proto-echo-mod.txt) (`v1.15`) : Protocoles `APE`, `PAP`, `PDI`, `PCC`, `PCD` (Contradiction Dialectique).
  - [`dev-app-echo-mod.txt`](modules/dev-app-echo-mod.txt) (`v1.5`) : Principe `PDSA` et commandes de cycle applicatif (`#BUILD`, `#RUN`, `#EDIT`, `#VALIDER`).
  - [`web-search-echo-mod.txt`](modules/web-search-echo-mod.txt) (`v3.3`) : Protocole de Recherche Web Autonome (`PRWA`).
- [`apps/`](apps/) : Exemples d'applications structurées (`*-echo-app.txt`) :
  - [`Presentation-echo-app.txt`](apps/Presentation-echo-app.txt) (`GénérateurPrésentation`, `v6.5`).
- [`docs/`](docs/) : Documentation HTML historique d'ECHO v4 ([`docs/index.html`](docs/index.html)) et logos associés.
- [`LICENSE`](LICENSE) : Licence Apache 2.0.

> **Développement actif** : L'architecture actuelle **ECHO Framework v5** se trouve sur les branches `main` et `dev`.
