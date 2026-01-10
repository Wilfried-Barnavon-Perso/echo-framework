"""
title: Token Stats Monitor
author: Wilfried BARNAVON
type: filter
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import time

class Filter:
    class Valves(BaseModel):
        display_mode: str = Field(default="compact", description="compact (footer) ou verbose (details)")

    def __init__(self):
        self.valves = self.Valves()

    def inlet(self, body: Dict[str, Any], __user__: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body["_start_time"] = time.time()
        return body

    def outlet(self, body: Dict[str, Any], __user__: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        messages = body.get("messages", [])
        if not messages:
            return body

        last_message = messages[-1]["content"]
        
        input_chars = sum(len(m["content"]) for m in messages[:-1])
        output_chars = len(last_message)
        
        # Estimation brute (1 token ~= 4 chars)
        est_input = input_chars // 4
        est_output = output_chars // 4
        
        stats_block = ""
        if self.valves.display_mode == "compact":
            stats_block = f"\n\n---\n<small>🧮 **ECHO Stats:** In: ~{est_input} | Out: ~{est_output} | Total: ~{est_input + est_output}</small>"
        else:
             stats_block = f"""
\n\n
<details>
<summary>📊 Statistiques de Consommation</summary>
| Métrique | Valeur |
| :--- | :--- |
| Entrée (est.) | {est_input} toks |
| Sortie (est.) | {est_output} toks |
| Total (est.) | {est_input + est_output} toks |
</details>
"""
        # Note: Open WebUI 0.6 ne modifie pas toujours le message final en streaming via l'outlet,
        # mais c'est l'implémentation standard pour les appels non-streamés.
        
        return body