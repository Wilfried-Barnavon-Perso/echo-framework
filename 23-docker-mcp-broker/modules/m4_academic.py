import json
import httpx
from mcp.server.fastmcp import FastMCP

def register_academic_tools(mcp: FastMCP):
    
    @mcp.tool()
    async def search_academic_papers(query: str, domain: str = "computer_science") -> str:
        """Cherche des papiers de recherche (arXiv/Semantic Scholar)."""
        return json.dumps([{"title": "MOCK PAPER", "abstract": "Mock abstract."}])

    @mcp.tool()
    async def get_macro_indicators(country_code: str) -> str:
        """Récupère les indicateurs macro-économiques (World Bank)."""
        return json.dumps({"PIB": "2.7T", "inflation": "2%"})
