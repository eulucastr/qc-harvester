import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

_DRIVER = None

def create_scraper():
    """Cria e cacheia um único driver Selenium para reutilização"""
    global _DRIVER
    if _DRIVER is None:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        _DRIVER = webdriver.Chrome(options=chrome_options)
    return _DRIVER

def close_scraper():
    global _DRIVER
    if _DRIVER is not None:
        _DRIVER.quit()
        _DRIVER = None

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
    driver = create_scraper()
    try:
        driver.get(page_url)

        # Aguarda o carregamento do conteúdo principal
        WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CLASS_NAME, "q-exam-item"))
        )

        # Aguarda um pouco mais para garantir carregamento completo
        time.sleep(2)

        soup = BeautifulSoup(driver.page_source, "html.parser")
    except Exception as e:
        raise RuntimeError(f"Erro ao acessar {page_url}: {e}")

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

    time.sleep(1)
    return tests


def scrape_tests(main_url: str, scraper_config: dict):
    driver = create_scraper()
    try:
        driver.get(main_url)

        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "q-page-results-title"))
        )

        time.sleep(2)

        soup = BeautifulSoup(driver.page_source, "html.parser")
    except Exception as e:
        raise RuntimeError(f"Erro ao acessar {main_url}: {e}")

    total_pages = handle_pagination(soup)
    tests = []

    for page in range(1, total_pages + 1):
        query_params = []
        for board in scraper_config.get("bancas", []):
            query_params.append(f"by_examining_board[]={board.get("codigo", "")}")
        for year in scraper_config.get("anos", []):
            query_params.append(f"application_year[]={year}")

        query_string = "&".join(query_params)
        page_url = f"{main_url}?{query_string}"

        print(page_url)
        print(f"Scraping página {page}...")
        tests.extend(get_tests_from_page(page_url))

    return tests
