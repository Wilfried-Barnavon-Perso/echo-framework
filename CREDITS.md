# 🌟 Crédits et Remerciements Open Source

ECHO Framework est une architecture d'orchestration qui se tient sur les épaules de géants. Ce projet ne pourrait exister sans les contributions massives de la communauté Open Source et des équipes de recherche qui publient leurs travaux sous licences permissives.

*« Et à tous les projets, petits ou grands, qui auraient pu être omis par inadvertance : merci. »*

---

## 1. Les Fondations (Tier 1)
Ces projets constituent la colonne vertébrale absolue du framework ECHO.

* **[Open WebUI](https://github.com/open-webui/open-webui)** : L'écosystème hôte et l'interface principale, que le Kernel ECHO utilise comme moteur de rendu et système nerveux central. (MIT License)
* **[Google Gemini](https://ai.google.dev/)** : L'intelligence brute et les modèles de raisonnement profonds (Pro / Flash / Lite) pilotés via l'API Google AI Studio.
* **[Qdrant](https://qdrant.tech/)** : Le moteur de base de données vectorielle ultra-performant, supportant la "Mémoire Organique V4" et la fusion sémantique. (Apache License 2.0)
* **[SQLite](https://www.sqlite.org/)** : Le moteur de base de données relationnelle embarqué assurant la persistance bit-perfect (les "Ombres Riches"). (Public Domain)
* **[Docker](https://www.docker.com/) & [Docker Compose](https://docs.docker.com/compose/)** : L'infrastructure de conteneurisation qui garantit l'isolation, la souveraineté et la portabilité d'ECHO. (Apache License 2.0)

---

## 2. Les Composants Périphériques (Tier 2)
*(Par ordre alphabétique)*

* **[Microsoft / Harrier-OSS](https://huggingface.co/microsoft/Harrier-OSS-v1-0.6B)** : Modèle d'embedding multilingue exécuté localement (ou sur Edge WebGPU) pour vectoriser la mémoire sans perte de confidentialité. (MIT License)
* **[BunkerWeb](https://github.com/bunkerity/bunkerweb) / ModSecurity** : Web Application Firewall (WAF) protégeant le trafic entrant de l'infrastructure Docker. (AGPL-3.0 License)
* **[Dulwich](https://www.dulwich.io/)** : Implémentation de Git en pur Python, utilisée par ECHO Codex pour le versioning transparent des fichiers modifiés. (GPL-2.0 / Apache License 2.0)
* **[edge-tts](https://github.com/rany2/edge-tts)** : Implémentation asynchrone du protocole Microsoft Edge Read-Aloud pour la synthèse vocale neuronale multilingue haute fidélité sans clé API. (LGPL-3.0 License)
* **[FFmpeg](https://ffmpeg.org/)** : Le couteau suisse du traitement multimédia, assurant l'encodage et le streaming audio à la volée. (GPL / LGPL)
* **[Gridstack](https://gridstackjs.com/)** : Moteur de layout dynamique utilisé par le Dashboard interactif de l'Admin Manager. (MIT License)
* **[httpx](https://www.python-httpx.org/)** : Client HTTP/2 asynchrone hautement performant, utilisé pour les requêtes furtives (Stealth) du Web Engine. (BSD-3-Clause License)
* **[MarkItDown](https://github.com/microsoft/markitdown)** : Bibliothèque Microsoft de conversion de fichiers Office (Word, Excel, PowerPoint) en Markdown structuré pour l'injection contextuelle. (MIT License)
* **[Monaco Editor](https://microsoft.github.io/monaco-editor/)** : Éditeur de code robuste (propulsant VS Code) intégré au HUD de ECHO Codex pour l'édition de fichiers. (MIT License)
* **[ONNX Runtime](https://onnxruntime.ai/)** : Moteur d'inférence multi-plateforme utilisé pour propulser la vectorisation WebGPU/WASM (`harrier-oss-v1-0.6b-ONNX`) au sein de l'Edge Embedding Bridge. (MIT License)
* **[Playwright](https://playwright.dev/)** : Framework d'automatisation de navigateur (Headless Chromium) au cœur de l'agent de navigation autonome. (Apache License 2.0)
* **[Pydub](https://github.com/jiaaro/pydub)** : Bibliothèque Python pour la manipulation et la normalisation des tampons audio en temps réel. (MIT License)
* **[llama.cpp](https://github.com/ggerganov/llama.cpp)** : Moteur d'inférence C/C++ haute performance exécutant le modèle GGUF (`harrier-oss-v1-0.6b.f16.gguf`) du Worker Embedding sur CPU en cas de fallback. (MIT License)
* **[SearXNG](https://github.com/searxng/searxng)** : Moteur de méta-recherche souverain préservant la confidentialité des recherches web du modèle. (AGPL-3.0 License)
* **[Watchtower](https://github.com/containrrr/watchtower)** : Utilitaire gérant la mise à jour automatisée des conteneurs isolés. (Apache License 2.0)

---
*Ce document est tenu à jour conformément aux standards de développement ECHO (Voir GEMINI.md).*
