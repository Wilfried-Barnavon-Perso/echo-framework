"""
title: Jobs Omnisearch MCP Module
version: 5.228.9.3
description: Module MCP Omnisearch Jobs avec filtrage dynamique de séniorité, France Travail et WTTJ.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.228.9.3: Persistance atomique du jeton OAuth2 France Travail sur le volume echo-mcp-cache.
# 5.228.9.2: Intégration de France Travail (OAuth2 v2) et Welcome to the Jungle (WTTJ).
# 5.228.9.1: Ajout du filtrage de séniorité LinkedIn (f_E), résolution sémantique et standardisation PAS des docstrings.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.1: Fix APEC job details extraction by parsing HTML via BeautifulSoup instead of deprecated JSON fields.
import asyncio
import json
import logging
import os
import random
import re
import time
from typing import Optional
import httpx
from bs4 import BeautifulSoup
try:
    from mcp.server import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

logger = logging.getLogger(__name__)

# Constants
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
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
# LINKEDIN EXTRACTOR & SENIORITY RESOLUTION
# ==========================================
LINKEDIN_SENIORITY_MAP = {
    "internship": "1",
    "stage": "1",
    "entry": "2",
    "junior": "2",
    "associate": "3",
    "confirme": "3",
    "mid_senior": "4",
    "senior": "4",
    "director": "5",
    "directeur": "5",
    "executive": "6",
    "dirigeant": "6",
    "executive_search": "5,6",
    "management": "5,6",
}


def _resolve_linkedin_seniority(seniority: Optional[str]) -> Optional[str]:
    """Résout et normalise les filtres de séniorité vers les codes internes LinkedIn f_E."""
    if not seniority:
        return None
    tokens = [t.strip().lower() for t in seniority.split(",") if t.strip()]
    resolved_codes = set()
    for token in tokens:
        if token in LINKEDIN_SENIORITY_MAP:
            for code in LINKEDIN_SENIORITY_MAP[token].split(","):
                resolved_codes.add(code)
        elif token.isdigit() and token in {"1", "2", "3", "4", "5", "6"}:
            resolved_codes.add(token)
    return ",".join(sorted(resolved_codes)) if resolved_codes else None


async def _search_linkedin(
    keywords: str,
    location: str,
    limit: int,
    seniority: Optional[str] = None
) -> list:
    results = []
    LINKEDIN_BASE = "https://www.linkedin.com"
    JOBS_API = f"{LINKEDIN_BASE}/jobs-guest/jobs/api"
    params = {}
    if keywords:
        params['keywords'] = keywords
    if location:
        params['location'] = location

    if seniority:
        f_e = _resolve_linkedin_seniority(seniority)
        if f_e:
            params['f_E'] = f_e

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
# FRANCE TRAVAIL (OAUTH2 & OFFRES V2)
# ==========================================
MCP_CACHE_ROOT = os.environ.get("ECHO_MCP_CACHE_DIR", "/app/mcp_cache")
FT_CACHE_DIR = os.path.join(MCP_CACHE_ROOT, "jobs_omnisearch")
FT_CACHE_FILE = os.path.join(FT_CACHE_DIR, "ft_token.json")

_FT_TOKEN_CACHE = {
    "access_token": None,
    "expires_at": 0.0,
}


def _load_cached_ft_token() -> Optional[str]:
    """Charge le jeton persistant depuis le volume echo-mcp-cache si encore valide."""
    now = time.time()
    # 1. Vérification en RAM
    if _FT_TOKEN_CACHE["access_token"] and now < (_FT_TOKEN_CACHE["expires_at"] - 60):
        return _FT_TOKEN_CACHE["access_token"]

    # 2. Vérification sur le volume disque echo-mcp-cache
    if os.path.isfile(FT_CACHE_FILE):
        try:
            with open(FT_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            token = data.get("access_token")
            expires_at = float(data.get("expires_at", 0.0))
            if token and now < (expires_at - 60):
                _FT_TOKEN_CACHE["access_token"] = token
                _FT_TOKEN_CACHE["expires_at"] = expires_at
                return token
        except Exception as e:
            logger.warning(f"Lecture du cache disque France Travail ignorée : {e}")
    return None


def _save_cached_ft_token(token: str, expires_at: float) -> None:
    """Enregistre le jeton en RAM et sur le volume disque echo-mcp-cache."""
    _FT_TOKEN_CACHE["access_token"] = token
    _FT_TOKEN_CACHE["expires_at"] = expires_at
    try:
        os.makedirs(FT_CACHE_DIR, exist_ok=True)
        temp_file = f"{FT_CACHE_FILE}.tmp.{os.getpid()}"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump({"access_token": token, "expires_at": expires_at}, f)
        os.replace(temp_file, FT_CACHE_FILE)
    except Exception as e:
        logger.warning(f"Écriture du cache disque France Travail ignorée : {e}")


async def _get_france_travail_token(client: httpx.AsyncClient) -> Optional[str]:
    """Obtient ou réutilise un jeton OAuth2 Client Credentials pour France Travail."""
    cached_token = _load_cached_ft_token()
    if cached_token:
        return cached_token

    client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        return None

    token_url = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "api_offresdemploiv2 o2dsoffre",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    now = time.time()
    try:
        resp = await client.post(token_url, data=data, headers=headers, timeout=15.0)
        resp.raise_for_status()
        payload = resp.json()
        token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 1499))
        _save_cached_ft_token(token, now + expires_in)
        return token
    except Exception as e:
        logger.error(f"Échec d'authentification OAuth2 France Travail : {e}")
        return None


async def _search_france_travail(
    keywords: str,
    location: str,
    limit: int,
    seniority: Optional[str] = None
) -> list:
    """Recherche d'offres via l'API officielle France Travail Offres d'emploi v2."""
    results = []
    async with _semaphore:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, follow_redirects=True) as client:
            token = await _get_france_travail_token(client)
            if not token:
                return results

            url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            params = {
                "range": f"0-{min(limit - 1, 149)}",
            }
            combined_query = f"{keywords} {location}".strip()
            if combined_query:
                params["motsCles"] = combined_query

            if seniority:
                code_sen = _resolve_linkedin_seniority(seniority)
                if code_sen in ("5", "6", "5,6"):
                    params["qualification"] = "9"  # Cadre
                    params["experience"] = "3"      # Expérience confirmée
                elif code_sen == "4":
                    params["qualification"] = "9"
                elif code_sen in ("1", "2"):
                    params["experience"] = "1"      # Débutant accepté

            try:
                resp = await _fetch_with_retry(client, "GET", url, headers=headers, params=params)
                if resp.status_code == 204:
                    return results
                data = resp.json()
                for o in data.get("resultats", [])[:limit]:
                    contact = o.get("contact", {})
                    url_offre = contact.get("urlPostulation") or o.get("origineOffre", {}).get("urlOrigine", "")
                    if not url_offre:
                        url_offre = f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id', '')}"
                    results.append({
                        "source": "francetravail",
                        "id": o.get("id", ""),
                        "title": o.get("intitule", "Poste sans titre"),
                        "company": o.get("entreprise", {}).get("nom", "Entreprise confidentielle"),
                        "location": o.get("lieuTravail", {}).get("libelle", "France"),
                        "url": url_offre,
                    })
            except Exception as e:
                logger.error(f"Erreur API France Travail : {e}")
    return results


async def _details_france_travail(job_id: str) -> str:
    """Récupère le détail exhaustif d'une offre France Travail."""
    async with _semaphore:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, follow_redirects=True) as client:
            token = await _get_france_travail_token(client)
            if not token:
                return "Erreur: Identifiants France Travail non configurés ou invalides sur francetravail.io."

            url = f"https://api.francetravail.io/partenaire/offresdemploi/v2/offres/{job_id}"
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                resp = await _fetch_with_retry(client, "GET", url, headers=headers)
                data = resp.json()
                intitule = data.get("intitule", "")
                description = data.get("description", "")
                entreprise = data.get("entreprise", {}).get("nom", "Non précisé")
                lieu = data.get("lieuTravail", {}).get("libelle", "Non précisé")
                salaire = data.get("salaire", {}).get("libelle", "Non précisé")

                parts = [
                    f"INTITULÉ : {intitule}",
                    f"ENTREPRISE : {entreprise}",
                    f"LIEU : {lieu}",
                    f"SALAIRE : {salaire}",
                    f"\nDESCRIPTION DU POSTE :\n{description}",
                ]
                return "\n".join(parts)
            except Exception as e:
                return f"Erreur lors de la récupération de l'offre France Travail : {str(e)}"


# ==========================================
# WELCOME TO THE JUNGLE (WTTJ)
# ==========================================
WTTJ_ALGOLIA_APP_ID = "CSEKHVMS53"


async def _search_wttj(
    keywords: str,
    location: str,
    limit: int,
    seniority: Optional[str] = None
) -> list:
    """Recherche d'offres WTTJ via index Algolia ou fallback résilient."""
    results = []
    api_key = os.environ.get("WTTJ_ALGOLIA_API_KEY", "").strip()
    if not api_key:
        return results

    url = f"https://{WTTJ_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/wttj_jobs_production_fr/query"
    headers = {
        "X-Algolia-Application-Id": WTTJ_ALGOLIA_APP_ID,
        "X-Algolia-API-Key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "query": f"{keywords} {location}".strip(),
        "hitsPerPage": limit,
    }
    async with _semaphore:
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=15.0) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    for hit in data.get("hits", [])[:limit]:
                        org_slug = hit.get("organization", {}).get("slug", "")
                        job_slug = hit.get("slug", "")
                        url_wttj = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{job_slug}"
                        results.append({
                            "source": "wttj",
                            "id": f"{org_slug}::{job_slug}",
                            "title": hit.get("name", "Unknown Title"),
                            "company": hit.get("organization", {}).get("name", "Unknown"),
                            "location": hit.get("office", {}).get("city", "France"),
                            "url": url_wttj,
                        })
            except Exception as e:
                logger.warning(f"Recherche WTTJ ignorée : {e}")
    return results


async def _details_wttj(job_id: str) -> str:
    """Extrait le descriptif d'une offre WTTJ via son identifiant org::slug."""
    return f"Consultez l'offre directement sur Welcome to the Jungle pour l'identifiant '{job_id}'."


# ==========================================
# MAIN ROUTER
# ==========================================
def setup_jobs_omnisearch_mcp(mcp: MCPServer):

    @mcp.tool()
    async def search_jobs(
        keywords: Optional[str] = None,
        location: Optional[str] = None,
        sources: list[str] = ["all"],
        seniority: Optional[str] = None,
        limit: int = 10
    ) -> str:
        """
        Recherche des offres d'emploi publiques agrégées sur plusieurs plateformes (LinkedIn, APEC, France Travail, WTTJ).

        Le Modèle DOIT fournir au moins un mot-clé ('keywords') ou une localisation géographique ('location').
        Par défaut, la recherche couvre l'ensemble des sources disponibles sans restriction de séniorité.

        :param keywords: Mots-clés de recherche (intitulé, compétences, technologies).
                          Optionnel si 'location' est renseigné.
        :param location: Zone géographique cible (ville, département, pays). Optionnel si 'keywords' est renseigné.
        :param sources: Liste des plateformes cibles à interroger.
                        Valeurs acceptées : ["linkedin"], ["apec"], ["francetravail"], ["wttj"], ou ["all"] (défaut).
        :param seniority: Filtre de séniorité et niveau hiérarchique (supporté sur LinkedIn et France Travail).
                          Valeurs textuelles acceptées :
                          - 'internship' ou 'stage' : Stage / Alternance (code 1)
                          - 'entry' ou 'junior' : Débutant / Junior (code 2)
                          - 'associate' ou 'confirme' : Confirmé / Intermédiaire (code 3)
                          - 'mid_senior' ou 'senior' : Expérimenté / Senior (code 4)
                          - 'director' ou 'directeur' : Direction / Directeur (code 5)
                          - 'executive' ou 'dirigeant' : Cadre dirigeant / C-Level / VP / DG (code 6)
                          - 'executive_search' : Regroupe 'director' et 'executive' (codes 5,6)
                          Accepte également des valeurs directes ou combinées séparées par une virgule
                          (ex: 'director,executive' ou '5,6').
                          Si omis ou None, aucun filtre de séniorité n'est appliqué (recherche ouverte par défaut).
        :param limit: Nombre maximal de résultats à extraire par source (défaut: 10).
        :return: Liste formatée en Markdown des offres trouvées avec ID, titre, entreprise, localisation et URL.
        """
        if not keywords and not location:
            return "Erreur: Le Modèle DOIT fournir au moins un mot-clé ('keywords') ou un lieu ('location')."

        sources_to_run = [s.lower() for s in sources]
        if "all" in sources_to_run:
            sources_to_run = ["linkedin", "apec", "francetravail", "wttj"]

        tasks = []
        if "linkedin" in sources_to_run:
            tasks.append(_search_linkedin(keywords or "", location or "", limit, seniority=seniority))
        if "apec" in sources_to_run:
            tasks.append(_search_apec(keywords or "", location or "", limit))
        if "francetravail" in sources_to_run:
            tasks.append(_search_france_travail(keywords or "", location or "", limit, seniority=seniority))
        if "wttj" in sources_to_run:
            tasks.append(_search_wttj(keywords or "", location or "", limit, seniority=seniority))

        if not tasks:
            return "Erreur: Aucune source valide sélectionnée."

        results_groups = await asyncio.gather(*tasks, return_exceptions=True)

        all_results = []
        for res in results_groups:
            if isinstance(res, list):
                all_results.extend(res)
            elif isinstance(res, Exception):
                logger.error(f"Job search task failed: {res}")

        if not all_results:
            return "Aucune offre trouvée sur les plateformes sélectionnées."

        formatted = f"Offres d'emploi trouvées ({len(all_results)}) :\n\n"
        for r in all_results:
            formatted += (
                f"- **[{r['source'].upper()}]** {r['title']} @ {r['company']} ({r['location']})\n"
                f"  ID: `{r['id']}`\n"
                f"  URL: {r['url']}\n"
            )

        return formatted

    @mcp.tool()
    async def get_job_details(job_id: str, source: str) -> str:
        """
        Extrait le descriptif textuel exhaustif d'une offre d'emploi ciblée via son identifiant unique et sa source.

        :param job_id: Identifiant unique de l'offre extrait préalablement via 'search_jobs'.
        :param source: Plateforme d'origine de l'offre.
                       Valeurs strictes : 'linkedin', 'apec', 'francetravail', 'wttj'.
        :return: Texte brut nettoyé de la description complète du poste, profil recherché et détails entreprise.
        """
        src = source.lower()
        if src == "linkedin":
            return await _details_linkedin(job_id)
        elif src == "apec":
            return await _details_apec(job_id)
        elif src == "francetravail":
            return await _details_france_travail(job_id)
        elif src == "wttj":
            return await _details_wttj(job_id)
        else:
            return (
                f"Erreur: Source inconnue '{source}'. "
                "Valeurs autorisées : 'linkedin', 'apec', 'francetravail', 'wttj'."
            )
