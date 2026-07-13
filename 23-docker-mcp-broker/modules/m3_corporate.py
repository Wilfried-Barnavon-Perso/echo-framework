import json
import httpx
from mcp.server.fastmcp import FastMCP

def register_corporate_tools(mcp: FastMCP):
    
    @mcp.tool()
    async def search_french_company(query: str) -> str:
        """Recherche une entreprise française (API Sirene)."""
        # TODO: Call https://recherche-entreprises.api.gouv.fr/search
        return json.dumps([{"siren": "123456789", "name": "MOCK CORP"}])

    @mcp.tool()
    async def check_bodacc_announcements(siren: str) -> str:
        """Vérifie le BODACC pour redressement ou liquidation."""
        return json.dumps({"status": "clean", "announcements": []})
