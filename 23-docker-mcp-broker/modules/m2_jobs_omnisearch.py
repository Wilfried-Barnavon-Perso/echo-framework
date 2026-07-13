"""
title: Jobs Omnisearch MCP Module
version: 1.1
description: 1.1: Fix APEC job details extraction by parsing HTML via BeautifulSoup instead of deprecated JSON fields.
"""
import asyncio
import random
import logging
import json
import re
from typing import Optional, List
import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Constants
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
}

_semaphore = asyncio.Semaphore(5)

async def _fetch_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Fetch URL with exponential backoff on 429."""
    max_retries = 3
    base_delay = 5.0
    await asyncio.sleep(random.uniform(0.5, 1.5))
    for attempt in range(max_retries):
        try:
            if method.upper() == 'GET':
                response = await client.get(url, timeout=30.0, **kwargs)
            else:
                response = await client.post(url, timeout=30.0, **kwargs)
                
            if response.status_code == 429:
                if attempt == max_retries - 1:
                    logger.error(f"HTTP 429: Too Many Requests on {url}")
                    response.raise_for_status()
                sleep_time = base_delay * (3 ** attempt) + random.uniform(0.5, 2.0)
                logger.warning(f"Rate limited (429). Retrying in {sleep_time:.2f}s...")
                await asyncio.sleep(sleep_time)
                continue
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                pass
            else:
                logger.error(f"HTTP Error {e.response.status_code} for {url}")
                raise
        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            raise

# ==========================================
# LINKEDIN EXTRACTOR
# ==========================================
async def _search_linkedin(keywords: str, location: str, limit: int) -> list:
    results = []
    LINKEDIN_BASE = "https://www.linkedin.com"
    JOBS_API = f"{LINKEDIN_BASE}/jobs-guest/jobs/api"
    params = {}
    if keywords: params['keywords'] = keywords
    if location: params['location'] = location

    start = 0
    chunk_size = 25
    async with _semaphore:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, follow_redirects=True) as client:
            while start < limit:
                params['start'] = start
                url = f"{JOBS_API}/seeMoreJobPostings/search"
                try:
                    resp = await _fetch_with_retry(client, 'GET', url, params=params)
                except Exception:
                    break
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                job_cards = soup.find_all('li')
                if not job_cards: break
                    
                for card in job_cards:
                    if len(results) >= limit: break
                    try:
                        title_el = card.find('h3', class_='base-search-card__title')
                        title = title_el.text.strip() if title_el else "Unknown Title"
                        company_el = card.find('h4', class_='base-search-card__subtitle')
                        company = company_el.text.strip() if company_el else "Unknown Company"
                        loc_el = card.find('span', class_='job-search-card__location')
                        loc = loc_el.text.strip() if loc_el else "Unknown Location"
                        link_el = card.find('a', class_='base-card__full-link')
                        url_link = link_el['href'] if link_el and 'href' in link_el.attrs else ""
                        
                        job_id = ""
                        div_el = card.find('div', attrs={'data-entity-urn': True})
                        if div_el:
                            job_id = div_el['data-entity-urn'].split(':')[-1]
                        if not job_id and url_link:
                            match = re.search(r'-(\d+)(?:\?|$)', url_link)
                            if match: job_id = match.group(1)
                        
                        if job_id:
                            results.append({
                                "source": "linkedin",
                                "id": job_id,
                                "title": title,
                                "company": company,
                                "location": loc,
                                "url": url_link.split('?')[0]
                            })
                    except Exception:
                        continue
                start += chunk_size
    return results

async def _details_linkedin(job_id: str) -> str:
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    async with _semaphore:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, follow_redirects=True) as client:
            try:
                resp = await _fetch_with_retry(client, 'GET', url)
            except Exception as e:
                return f"Error fetching LinkedIn job details: {str(e)}"
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            desc_div = soup.find('div', class_='show-more-less-html__markup')
            if desc_div:
                for br in desc_div.find_all("br"): br.replace_with("\n")
                text = desc_div.get_text(separator='\n').strip()
                return re.sub(r'\n{3,}', '\n\n', text)
            return soup.get_text(separator='\n', strip=True)

# ==========================================
# APEC EXTRACTOR
# ==========================================
async def _search_apec(keywords: str, location: str, limit: int) -> list:
    results = []
    url = "https://www.apec.fr/cms/webservices/rechercheOffre"
    
    # L'Apec utilise des codes complexes pour les lieux (ex: 71138 pour Paris). 
    # Pour simplifier et fiabiliser la recherche via MCP, on injecte la localisation directement dans les mots-clés
    # car le moteur full-text de l'Apec le gère très bien.
    combined_query = f"{keywords} {location}".strip()
    
    payload = {
        "motsCles": combined_query,
        "pagination": {"startIndex": 0, "range": limit}
    }
    
    headers = DEFAULT_HEADERS.copy()
    headers["Content-Type"] = "application/json"
    
    async with _semaphore:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            try:
                resp = await _fetch_with_retry(client, 'POST', url, json=payload)
                data = resp.json()
                resultats = data.get('resultats', [])
                for o in resultats[:limit]:
                    results.append({
                        "source": "apec",
                        "id": o.get('numeroOffre', ''),
                        "title": o.get('intitule', 'Unknown Title'),
                        "company": o.get('nomCommercial', 'Unknown Company'),
                        "location": o.get('lieuTexte', 'Unknown Location'),
                        "url": f"https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/{o.get('numeroOffre','')}"
                    })
            except Exception as e:
                logger.error(f"APEC search error: {e}")
                results.append({
                    "source": "apec_error",
                    "id": "error",
                    "title": f"DEBUG ERROR: {type(e).__name__}",
                    "company": str(e),
                    "location": "N/A",
                    "url": "N/A"
                })
    return results

async def _details_apec(job_id: str) -> str:
    url = f"https://www.apec.fr/cms/webservices/offre/public?numeroOffre={job_id}"
    async with _semaphore:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, follow_redirects=True) as client:
            try:
                resp = await _fetch_with_retry(client, 'GET', url)
                data = resp.json()
                
                desc_html = data.get('texteHtml', '')
                profil_html = data.get('texteHtmlProfil', '')
                entreprise_html = data.get('texteHtmlEntreprise', '')
                
                def extract_text(html_content: str) -> str:
                    if not html_content:
                        return ""
                    soup = BeautifulSoup(html_content, 'html.parser')
                    for br in soup.find_all("br"): br.replace_with("\n")
                    text = soup.get_text(separator='\n').strip()
                    return re.sub(r'\n{3,}', '\n\n', text)
                
                desc = extract_text(desc_html)
                profil = extract_text(profil_html)
                entreprise = extract_text(entreprise_html)
                
                parts = []
                if desc:
                    parts.append(f"DESCRIPTION:\n{desc}")
                if profil:
                    parts.append(f"PROFIL RECHERCHÉ:\n{profil}")
                if entreprise:
                    parts.append(f"ENTREPRISE:\n{entreprise}")
                    
                if not parts:
                    return "No details available."
                    
                return "\n\n".join(parts)
            except Exception as e:
                return f"Error fetching APEC job details: {str(e)}"

# ==========================================
# MAIN ROUTER
# ==========================================
def setup_jobs_omnisearch_mcp(mcp: FastMCP):
    
    @mcp.tool()
    async def search_jobs(
        keywords: Optional[str] = None,
        location: Optional[str] = None,
        sources: list[str] = ["all"],
        limit: int = 10
    ) -> str:
        """
        Search for public job postings across LinkedIn and APEC.
        Sources can be a list of strings: ["linkedin", "apec"] or ["all"].
        Returns a formatted Markdown list of consolidated job results.
        """
        if not keywords and not location:
            return "Error: You must provide either keywords or a location to search."
            
        sources_to_run = [s.lower() for s in sources]
        if "all" in sources_to_run:
            sources_to_run = ["linkedin", "apec"]
            
        tasks = []
        if "linkedin" in sources_to_run:
            tasks.append(_search_linkedin(keywords or "", location or "", limit))
        if "apec" in sources_to_run:
            tasks.append(_search_apec(keywords or "", location or "", limit))
            
        if not tasks:
            return "Error: Invalid source selected."
            
        results_groups = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_results = []
        for res in results_groups:
            if isinstance(res, list):
                all_results.extend(res)
            elif isinstance(res, Exception):
                logger.error(f"Job search task failed: {res}")
                
        if not all_results:
            return "No jobs found across the selected platforms."
            
        formatted = f"Found {len(all_results)} jobs:\n\n"
        for r in all_results:
            formatted += f"- **[{r['source'].upper()}]** {r['title']} @ {r['company']} ({r['location']})\n  ID: `{r['id']}`\n  URL: {r['url']}\n"
            
        return formatted

    @mcp.tool()
    async def get_job_details(job_id: str, source: str) -> str:
        """
        Get the full text description of a specific job posting by its ID and source.
        Valid sources: 'linkedin', 'apec'.
        """
        src = source.lower()
        if src == "linkedin":
            return await _details_linkedin(job_id)
        elif src == "apec":
            return await _details_apec(job_id)
        else:
            return f"Error: Unknown source '{source}'."
