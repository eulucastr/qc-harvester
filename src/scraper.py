import cloudscraper
from bs4 import BeautifulSoup

def handle_pagination(soup):
    total_pages = 1
    title_tag = soup.find("h2", class_="q-page-results-title")
    if title_tag:
        strong_tag = title_tag.find("strong")
        if strong_tag:
            try:
                num_provas = int(strong_tag.get_text(strip=True).replace(".", ""))
                total_pages = (num_provas // 20) + (1 if num_provas % 20 != 0 else 0)
            except ValueError:
                pass
    return total_pages


def get_tests_from_page(page_url):
    scraper = cloudscraper.create_scraper()
    try:
        response = scraper.get(page_url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Erro ao acessar {page_url}: {e}")

    soup = BeautifulSoup(response.text, "html.parser")
    tests = []

    for item in soup.select(".q-exam-item"):
        test = {}

        title_span = item.select_one(".q-title")
        if title_span:
            title_parts = [part.strip() for part in title_span.get_text().split(" - ")]
            if len(title_parts) >= 1:
                test["banca"] = title_parts[0]
            if len(title_parts) >= 2:
                test["ano"] = title_parts[1]
            if len(title_parts) >= 3:
                test["órgão"] = title_parts[2]
            if len(title_parts) >= 4:
                test["cargo"] = title_parts[3]
            if len(title_parts) >= 5:
                test["função"] = title_parts[4].replace("Função:", "").strip()

        date_span = item.select_one(".q-date")
        if date_span:
            test["aplicação"] = (
                date_span.get_text(strip=True).replace("Aplicada em", "").strip()
            )

        level_span = item.select_one(".q-level")
        if level_span:
            test["escolaridade"] = level_span.get_text(strip=True)

        dropdown = item.select_one(".dropdown-menu")
        if dropdown:
            for link in dropdown.find_all("a"):
                text = link.get_text(strip=True).lower()
                href = link.get("href")
                if "prova" in text:
                    test["prova"] = href
                elif "gabarito" in text:
                    test["gabarito"] = href
                elif "alterações" in text or "alteracoes" in text:
                    test["alterações"] = href
                elif "edital" in text:
                    test["edital"] = href

        tests.append(test)

    return tests


def scrape_tests(main_url: str, scraper_config: dict):
    scraper = cloudscraper.create_scraper()
    try:
        response = scraper.get(main_url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Erro ao acessar {main_url}: {e}")

    soup = BeautifulSoup(response.text, "html.parser")

    total_pages = handle_pagination(soup)
    tests = []

    for page in range(1, total_pages + 1):
        query_params = []
        for key, values in scraper_config.items():
            if isinstance(values, list):
                for val in values:
                    query_params.append(f"{key}[]={val}")
            else:
                query_params.append(f"{key}={values}")
        query_params.append(f"page={page}")

        query_string = "&".join(query_params)
        page_url = f"{main_url}?{query_string}"

        tests.extend(get_tests_from_page(page_url))

    return tests
