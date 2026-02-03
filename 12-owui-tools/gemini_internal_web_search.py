"""
title: ECHO Gemini Web Search
author: Wilfried BARNAVON
version: 11.1
description: 11.1: Migration Auth SQLite. Recherche Google via API Gemini interne, Grounding et sortie JSON.
"""

# ECHO CONFIG NAME : ECHO Gemini Web Search Tool

import json, os, requests, uuid, random, sqlite3
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        search_model_id: str = Field(default="gemini-3-flash-preview", description="ID du modèle.")
        max_output_tokens: int = Field(default=65536, description="Max Tokens.")
        debug_mode: bool = Field(default=False, description="Debug.")

    def __init__(self):
        self.valves = self.Valves()
        self.base_url = "https://cloudcode-pa.googleapis.com/v1internal"
        self.data_dir = "/app/backend/data"

    def _get_credentials_from_db(self, user_id: str):
        """
        Récupère le token et le project_id depuis la base SQLite de l'utilisateur.
        Remplace l'ancienne méthode basée sur les fichiers JSON.
        """
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        db_path = os.path.join(self.data_dir, "user_dbs", f"user-{safe_uid}.db")

        if not os.path.exists(db_path):
            return None, None, f"Base de données introuvable pour l'utilisateur ({db_path})"

        try:
            # Connexion en lecture seule pour éviter les verrous
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
            cursor = conn.cursor()
            
            # Récupération Token
            cursor.execute("SELECT value FROM auth_data WHERE key = 'google_token'")
            row_token = cursor.fetchone()
            token = None
            if row_token:
                try:
                    # Le token est stocké sous forme de JSON complet (credentials object)
                    creds_json = json.loads(row_token[0])
                    token = creds_json.get("token")
                except: pass

            # Récupération Project ID
            cursor.execute("SELECT value FROM auth_data WHERE key = 'google_project_id'")
            row_pid = cursor.fetchone()
            project_id = row_pid[0] if row_pid else None

            conn.close()
            return token, project_id, None
            
        except Exception as e:
            return None, None, str(e)

    def gemini_internal_web_search(self, query: str, current_date: str, current_time: str, location: str, __user__: dict = {}) -> str:
        """
        [SEARCH ENGINE] Performs a Google Search to retrieve real-time information, news, weather, or facts.
        
        Use this tool when:
        - You need up-to-date information (news, sports scores, weather).
        - You need to verify a fact.
        - You need a summary of a topic from multiple sources.
        
        Do NOT use this tool to browse a specific URL (use Advanced Web Browser for that).
        Returns a synthesized summary in French with source links.
        """
        if self.valves.debug_mode: print(f"\n[SEARCH v11.1] Query='{query}' User={__user__.get('id', 'Unknown')}")
        
        # 1. Vérification User Isolation
        if not __user__ or "id" not in __user__:
             return json.dumps({"error": "Erreur critique: Utilisateur non identifié. Impossible d'accéder aux tokens personnels."}, ensure_ascii=False)

        user_id = __user__["id"]
        
        # 2. Récupération Credentials via SQLite
        token, project_id, error_msg = self._get_credentials_from_db(user_id)
        
        if error_msg:
             if self.valves.debug_mode: print(f"[SEARCH ERROR] DB Error: {error_msg}")
             return json.dumps({"error": f"Erreur système (DB): {error_msg}"}, ensure_ascii=False)
        
        if not token or not project_id: 
            return json.dumps({"error": "Authentification Google manquante. Veuillez envoyer un message standard au modèle pour qu'il initie le flux d'authentification OAuth/PKCE, puis réessayez."}, ensure_ascii=False)

        # 3. Exécution de la requête API
        context_prompt = f"Contexte: Nous sommes à {location}, le {current_date} et il est {current_time}.\nRequête: {query}\nConsigne: Effectue la recherche Google nécessaire et synthétise la réponse en français."
        clean_project_id = project_id.replace("projects/", "")
        endpoint = f"{self.base_url}:streamGenerateContent?alt=sse"
        
        headers = {
            "Authorization": f"Bearer {token}", 
            "Content-Type": "application/json", 
            "User-Agent": "GeminiCLI/0.24.0", # Aligné avec Pipe Engine v140
            "x-goog-api-client": "gl-python/3.10"
        }
        
        payload = {
            "model": self.valves.search_model_id, 
            "project": clean_project_id, 
            "user_prompt_id": hex(random.getrandbits(64))[2:], 
            "request": {
                "contents": [{"role": "user", "parts": [{"text": context_prompt}]}], 
                "session_id": str(uuid.uuid4()),  
                "tools": [{"google_search": {}}, {"urlContext": {}}], 
                "generationConfig": {
                    "temperature": 1.0, 
                    "maxOutputTokens": self.valves.max_output_tokens, 
                    "thinkingConfig": {"thinkingLevel": "HIGH"}, 
                    "responseMimeType": "application/json"
                }
            }
        }

        try:
            response = requests.post(endpoint, json=payload, headers=headers, stream=True, timeout=30)
            
            if response.status_code == 401:
                 return json.dumps({"error": "Token Google expiré ou invalide. Veuillez rafraîchir votre session via le chat principal."}, ensure_ascii=False)
            
            if response.status_code != 200: 
                return json.dumps({"error": f"API Error {response.status_code}", "details": response.text[:500]})
            
            final_text, source_list, seen_urls = "", [], set()
            
            # Parsing SSE
            for line in response.iter_lines():
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data:"):
                        try:
                            json_str = decoded[5:].strip()
                            if not json_str: continue
                            data = json.loads(json_str)
                            cand = data.get("response", {}).get("candidates", [])[0]
                            
                            # Extraction contenu texte
                            if "content" in cand:
                                parts = cand["content"].get("parts", [])
                                if parts and "text" in parts[0]: final_text += parts[0]["text"]
                            
                            # Extraction Grounding (Sources)
                            grounding = cand.get("groundingMetadata", {})
                            chunks = grounding.get("groundingChunks", [])
                            for chunk in chunks:
                                web = chunk.get("web", {})
                                uri, title = web.get("uri"), web.get("title", "Source")
                                if uri and uri not in seen_urls: 
                                    seen_urls.add(uri)
                                    source_list.append({"title": title, "url": uri})
                        except: pass
            
            if not final_text: return json.dumps({"result": "Aucun résultat trouvé ou réponse vide de l'API."}, ensure_ascii=False)
            
            return json.dumps({
                "query": query, 
                "context": f"{location}, {current_date} {current_time}", 
                "summary": final_text, 
                "sources": source_list
            }, ensure_ascii=False)

        except Exception as e: 
            return json.dumps({"error": "Exception outil interne", "details": str(e)}, ensure_ascii=False)
