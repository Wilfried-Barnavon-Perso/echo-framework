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
