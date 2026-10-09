<RULE[Mise à Jour Documentaire (ECHO Framework)]>
1. **Vérité Absolue du Code** : Le code source est la SEULE source de vérité. Si la documentation contredit le code, la documentation est fausse. N'inventez jamais de concepts architecturaux (ex: fenêtres glissantes) non vérifiables dans l'état actuel du code.
2. **Intégrité Structurelle HTML** : Lors de la mise à jour des fichiers docs/*.html, il est STRICTEMENT INTERDIT de modifier, casser ou altérer la structure du DOM, les classes CSS, ou les identifiants. Seul le texte contenu dans les balises doit être amendé.
3. **Directive SNT (Zéro Marketing)** : Le ratio Signal/Bruit doit être maximal. Purgez tout adjectif promotionnel, superlatif ou formulation évasive. Adoptez un ton impersonnel, direct, académique et strictement technique. *(Exception : Le fichier `README.md` est exempté de cette règle et doit conserver un ton impactant, vitrine et "marketing").*
</RULE[Mise à Jour Documentaire (ECHO Framework)]>

<RULE[Méthodologie Top-Down (Anti-Vibe-Coding)]>
1. **L'Analyse Précède l'Action** : Face à toute demande de modification, de conception ou de débogage complexe sur le framework ECHO, tu DOIS systématiquement commencer par une phase d'analyse (recherche dans les fichiers, compréhension de l'architecture).
2. **Interdiction de Coder Sans Plan** : Ne génère, ne modifie et n'écris AUCUN code métier sans avoir préalablement soumis un plan d'implémentation (HLD, algorithme) à l'Utilisateur.
3. **Attente de Validation** : Une fois ton plan ou ton analyse exposée, arrête-toi et attends l'approbation explicite de l'Utilisateur (ex: "OK", "Fais-le") avant de déclencher des outils de modification de fichiers (`replace_file_content`, `write_to_file`).
</RULE[Méthodologie Top-Down (Anti-Vibe-Coding)]>
