import cloudscraper

# Cria um scraper único e reutilizável (não recria a cada request)
_SCRAPER = None

def scrape_page(page_url):
    """Cria e cacheia um único scraper para reutilização"""
    global _SCRAPER
    if _SCRAPER is None:
        _SCRAPER = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
    
    response = None
    try:
        response = _SCRAPER.get(
            page_url, 
            timeout=10,
            headers=get_headers()
        )
        response.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Erro ao acessar {page_url}: {e}")

    return response

def get_headers():
    """Headers que o Postman envia automaticamente"""
    
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.qconcursos.com/',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
    }