"""
title: Python Code Executor (Sidecar - User Aware)
author: Wilfried BARNAVON
version: 5.0
description: 5.0: Exécution de code Python via micro-service worker.
"""
import requests, json
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        execution_timeout: int = Field(default=30, description="Temps maximum (s).")
        worker_url: str = Field(default="http://python-worker:5000/execute", description="URL interne.")
        debug_mode: bool = Field(default=False, description="Activer les logs debug.")

    def __init__(self):
        self.valves = self.Valves()

    def python_code_executor(self, code: str, __user__: dict = {}) -> str:
        """
        Exécute du code Python arbitraire dans un conteneur sécurisé (Worker).
        L'identité de l'utilisateur est transmise au worker pour audit/isolation.
        """
        # Récupération de l'ID utilisateur (défaut 'anonymous' si appel système)
        user_id = __user__.get("id", "anonymous")
        
        if self.valves.debug_mode:
            print(f"[PY-EXEC v137.0] User={user_id} CodeLen={len(code)}")

        try:
            payload = {"code": code, "timeout": self.valves.execution_timeout}
            
            # Propagation de l'identité vers le worker via Headers HTTP
            headers = {
                "X-OpenWebUI-User-Id": str(user_id)
            }
            
            http_timeout = self.valves.execution_timeout + 2
            
            response = requests.post(
                self.valves.worker_url, 
                json=payload, 
                headers=headers, 
                timeout=http_timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success": 
                    return json.dumps({"status": "success", "output": data.get("output", "Aucune sortie.")}, ensure_ascii=False)
                else: 
                    return json.dumps({"status": "error", "error": data.get("error", "Erreur inconnue")}, ensure_ascii=False)
            else: 
                return json.dumps({"status": "error", "error": f"Erreur HTTP Worker: {response.status_code}"}, ensure_ascii=False)
                
        except requests.exceptions.ConnectionError: 
            return json.dumps({"status": "critical_error", "error": "Impossible de contacter le conteneur 'python-worker'. Vérifiez qu'il est démarré."}, ensure_ascii=False)
        except Exception as e: 
            return json.dumps({"status": "critical_error", "error": f"Erreur Client: {str(e)}"}, ensure_ascii=False)