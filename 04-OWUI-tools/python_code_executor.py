"""
title: Python Code Executor (Sidecar)
author: Wilfried BARNAVON
version: 4.1
"""
import requests, json
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        execution_timeout: int = Field(default=30, description="Temps maximum (s).")
        worker_url: str = Field(default="http://python-worker:5000/execute", description="URL interne.")

    def __init__(self):
        self.valves = self.Valves()

    def python_code_executor(self, code: str) -> str:
        try:
            payload = {"code": code, "timeout": self.valves.execution_timeout}
            http_timeout = self.valves.execution_timeout + 2
            response = requests.post(self.valves.worker_url, json=payload, timeout=http_timeout)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success": return json.dumps({"status": "success", "output": data.get("output", "Aucune sortie.")}, ensure_ascii=False)
                else: return json.dumps({"status": "error", "error": data.get("error", "Erreur inconnue")}, ensure_ascii=False)
            else: return json.dumps({"status": "error", "error": f"Erreur HTTP Worker: {response.status_code}"}, ensure_ascii=False)
        except requests.exceptions.ConnectionError: return json.dumps({"status": "critical_error", "error": "Impossible de contacter le conteneur 'python-worker'."}, ensure_ascii=False)
        except Exception as e: return json.dumps({"status": "critical_error", "error": f"Erreur Client: {str(e)}"}, ensure_ascii=False)