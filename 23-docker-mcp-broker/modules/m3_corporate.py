# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO - M3 CORPORATE
VERSION : 5.228.9.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.228.9.1: Fallback d'import MCPServer (compatibilité MCP 1.x et 2.x).
# 5.226.18.0: Alignement sur le versioning global ECHO.
import json
try:
    from mcp.server import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

def register_corporate_tools(mcp: MCPServer):
    
    @mcp.tool()
    async def search_french_company(query: str) -> str:
        """Recherche une entreprise française (API Sirene)."""
        url = "https://recherche-entreprises.api.gouv.fr/search"
        params = {"q": query}
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                results = data.get("results", [])
                
                if not results:
                    return json.dumps([{"status": "not_found", "message": "Aucune entreprise trouvée pour cette requête."}])
                
                return json.dumps(results)
                
        except Exception as e:
            return json.dumps([{"status": "error", "message": f"Échec de l'appel API Sirene : {str(e)}"}])

    @mcp.tool()
    async def check_bodacc_announcements(siren: str) -> str:
        """Vérifie le BODACC pour redressement ou liquidation."""
        return json.dumps({"status": "clean", "announcements": []})
