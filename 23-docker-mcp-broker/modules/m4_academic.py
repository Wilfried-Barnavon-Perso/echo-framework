import json
import httpx
import xml.etree.ElementTree as ET
from mcp.server import MCPServer

def register_academic_tools(mcp: MCPServer):
    
    @mcp.tool()
    async def search_academic_papers(query: str, domain: str = "computer_science") -> str:
        """Cherche des papiers de recherche (arXiv/Semantic Scholar)."""
        url = f"https://export.arxiv.org/api/query?search_query=all:{query}&max_results=5"
        
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                root = ET.fromstring(response.text)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                
                results = []
                for entry in root.findall("atom:entry", ns):
                    title = entry.find("atom:title", ns)
                    abstract = entry.find("atom:summary", ns)
                    author = entry.find("atom:author/atom:name", ns)
                    
                    results.append({
                        "title": title.text.strip().replace("\n", " ") if title is not None else "",
                        "author": author.text.strip() if author is not None else "",
                        "abstract": abstract.text.strip().replace("\n", " ") if abstract is not None else ""
                    })
                
                if not results:
                    return json.dumps([{"status": "not_found", "message": "Aucun papier trouvé pour cette requête."}])
                
                return json.dumps(results)
                
        except Exception as e:
            return json.dumps([{"status": "error", "message": f"Échec de l'appel arXiv : {str(e)}"}])


    @mcp.tool()
    async def get_macro_indicators(country_code: str) -> str:
        """Récupère les indicateurs macro-économiques (World Bank)."""
        url = f"https://api.worldbank.org/v2/country/{country_code}/indicator/NY.GDP.MKTP.CD?format=json&per_page=5"
        
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                return json.dumps(response.json())
                
        except Exception as e:
            return json.dumps([{"status": "error", "message": f"Échec de l'appel World Bank : {str(e)}"}])


