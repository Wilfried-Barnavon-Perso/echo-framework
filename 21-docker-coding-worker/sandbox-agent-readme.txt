=== DOCUMENTATION ECHO SANDBOX ===

Vous exécutez du code dans un environnement cloisonné (Python 3.14 / Node 22).

1. LIBRAIRIES MAISON (ECHO-Monitor)
-----------------------------------
La librairie `echo_monitor` permet d'afficher des interfaces web complexes (HTML, CSS, JavaScript complet) sous forme de fenêtres flottantes interactives à l'utilisateur. 
Le code injecté est exécuté de manière sécurisée (Iframe Sandbox), permettant de générer des graphiques ou des applications autonomes.

- Python :
  import echo_monitor
  html = "<h1>Graphique</h1><script>alert('JS fonctionne !');</script>"
  echo_monitor.display(html, title="Mon Analyse", width="800px", height="600px")

- Node.js :
  const echo_monitor = require('echo_monitor');
  const html = "<h1>Graphique</h1><script>alert('JS fonctionne !');</script>";
  echo_monitor.display(html, "Mon Analyse", "800px", "600px");

2. LIBRAIRIES PRE-INSTALLEES
----------------------------
- Python : requests, httpx, pandas, numpy, scipy, beautifulsoup4, lxml, Pillow, pydantic, sqlalchemy, openpyxl, PyPDF2
- Node.js : axios, cheerio, js-yaml, lodash, papaparse, mathjs, zod, dotenv, moment

Si vous avez besoin d'autres librairies, ajoutez-les dans l'argument `dependencies` de l'outil Code Executor.

3. CANAL DE DONNÉES DESCENDANT (PASSE-PLAT UI -> SANDBOX)
---------------------------------------------------------
Si l'utilisateur vous demande de traiter un fichier (ex: un enregistrement audio, une image du canvas) qu'il fournirait à travers l'UI, vous devez procéder ainsi :
1. Appelez l'outil `code_executor` en activant le paramètre : `fetch_ui_payload=True`.
2. Dans le code (Python/Node.js) que vous générez, récupérez le dictionnaire des données en appelant la librairie :

   - Python :
     import echo_monitor
     data = echo_monitor.get_ui_payload(window_id="votre_id_fenetre") # "default" par défaut
     if data and 'audio_b64' in data: ...

   - Node.js :
     const echo_monitor = require('echo_monitor');
     const data = echo_monitor.get_ui_payload("votre_id_fenetre");

Le décodage Base64 DOIT se faire dans votre script. En effet, l'IA ne reçoit JAMAIS directement ces données pour préserver sa RAM.

4. CANAUX BIDIRECTIONNELS TEMPS-RÉEL (UI <-> BACKEND SANDBOX)
-------------------------------------------------------------
L'architecture native embarque un Pipe Bidirectionnel asynchrone fonctionnant par polling de fichiers (Bind Mount).
- Flux Remontant (Upsert) : `echo_monitor.display(html, window_id="mon_id")` met à jour l'UI *pendant l'exécution*. IMPORTANT : L'utilisation du même `window_id` écrase et remplace la fenêtre existante au lieu d'en créer une nouvelle. Idéal pour animer des graphiques ou des barres de progression !
- Flux Descendant (Multiplexé) : L'UI peut communiquer avec votre script (via iframe isolée) en émettant `window.parent.postMessage({ echoUIPayload: { window_id: "mon_id", payload: { x: 10 } } }, "*");`. Relisez ces données en boucle (ex: via `while True:` et `time.sleep()`) depuis Python/Node avec `get_ui_payload("mon_id")`.
- Kill Autonome & Superviseur : N'implémentez PAS de bouton "Annuler" ou de logique de fermeture complexe. L'infrastructure ECHO injecte un Heartbeat invisible dans votre interface. Si l'utilisateur ferme votre fenêtre flottante via la croix native, le Superviseur détectera la mort du composant et tuera automatiquement et instantanément votre script en arrière-plan. Codez simplement votre boucle de traitement (`while True:`) sans vous soucier des fuites mémoire ni vérifier `is_window_closed()`.
