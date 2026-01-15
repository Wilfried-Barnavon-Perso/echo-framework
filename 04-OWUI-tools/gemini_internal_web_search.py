"""
title: Gemini Internal Web Search
author: Wilfried BARNAVON
version: v9.1
"""
import json, os, requests, uuid, random
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        search_model_id: str = Field(default="gemini-3-flash-preview", description="ID du modèle.")
        max_output_tokens: int = Field(default=65536, description="Max Tokens.")
        debug_mode: bool = Field(default=False, description="Debug.")

    def __init__(self):
        self.valves = self.Valves()
        self.base_url = "https://cloudcode-pa.googleapis.com/v1internal"

    def gemini_internal_search_web(self, query: str, current_date: str, current_time: str, location: str) -> str:
        if self.valves.debug_mode: print(f"\n[SEARCH v9.1] Query='{query}'")
        data_dir = "/app/backend/data"
        token_path, proj_path = os.path.join(data_dir, "gemini_official_token.json"), os.path.join(data_dir, "gemini_internal_project.txt")
        token, project_id = None, None
        try:
            if os.path.exists(token_path):
                with open(token_path, "r") as f: token = json.load(f).get("token")
            if os.path.exists(proj_path):
                with open(proj_path, "r") as f: project_id = f.read().strip()
        except Exception as e: return json.dumps({"error": f"Erreur lecture config: {str(e)}"}, ensure_ascii=False)
        if not token or not project_id: return json.dumps({"error": "Auth manquante."}, ensure_ascii=False)

        context_prompt = f"Contexte: Nous sommes à {location}, le {current_date} et il est {current_time}.\nRequête: {query}\nConsigne: Effectue la recherche Google nécessaire et synthétise la réponse en français."
        clean_project_id = project_id.replace("projects/", "")
        endpoint = f"{self.base_url}:streamGenerateContent?alt=sse"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "GeminiCLI/0.20.0", "x-goog-api-client": "gl-python/3.10"}
        payload = {"model": self.valves.search_model_id, "project": clean_project_id, "user_prompt_id": hex(random.getrandbits(64))[2:], "request": {"contents": [{"role": "user", "parts": [{"text": context_prompt}]}], "session_id": str(uuid.uuid4()), "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": self.valves.max_output_tokens}}}

        try:
            response = requests.post(endpoint, json=payload, headers=headers, stream=True, timeout=30)
            if response.status_code != 200: return json.dumps({"error": f"API Error {response.status_code}", "details": response.text}, ensure_ascii=False)
            final_text, source_list, seen_urls = "", [], set()
            for line in response.iter_lines():
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data:"):
                        try:
                            json_str = decoded[5:].strip()
                            if not json_str: continue
                            data = json.loads(json_str)
                            cand = data.get("response", {}).get("candidates", [])[0]
                            if "content" in cand:
                                parts = cand["content"].get("parts", [])
                                if parts and "text" in parts[0]: final_text += parts[0]["text"]
                            grounding = cand.get("groundingMetadata", {})
                            chunks = grounding.get("groundingChunks", [])
                            for chunk in chunks:
                                web = chunk.get("web", {})
                                uri, title = web.get("uri"), web.get("title", "Source")
                                if uri and uri not in seen_urls: seen_urls.add(uri); source_list.append({"title": title, "url": uri})
                        except: pass
            if not final_text: return json.dumps({"result": "Aucun résultat trouvé."}, ensure_ascii=False)
            return json.dumps({"query": query, "context": f"{location}, {current_date} {current_time}", "summary": final_text, "sources": source_list}, ensure_ascii=False)
        except Exception as e: return json.dumps({"error": "Exception outil", "details": str(e)}, ensure_ascii=False)