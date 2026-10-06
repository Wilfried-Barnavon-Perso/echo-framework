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

     # Arrêt propre (Graceful Shutdown)
     if echo_monitor.is_window_closed("votre_id_fenetre"):
         print("Interface fermée, arrêt du traitement.")
         exit(0)

   - Node.js :
     const echo_monitor = require('echo_monitor');
     const data = echo_monitor.get_ui_payload("votre_id_fenetre");
     if (echo_monitor.is_window_closed("votre_id_fenetre")) { process.exit(0); }

Le décodage Base64 DOIT se faire dans votre script. En effet, l'IA ne reçoit JAMAIS directement ces données pour préserver sa RAM.
