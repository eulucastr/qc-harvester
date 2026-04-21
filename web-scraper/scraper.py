import re
import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .helpers import parse_title_parts

from .exporters import log_error

_DRIVER = None
_PAGES_COUNT = 0
MAX_PAGES_BEFORE_RESTART = (
    50  # Reinicia o navegador a cada 50 páginas para evitar lentidão
)


def create_scraper(force_restart=False):
    """Cria e cacheia um único driver Selenium com configurações otimizadas"""
    global _DRIVER, _PAGES_COUNT

    if force_restart and _DRIVER is not None:
        close_scraper()

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

        # Otimizações extras para estabilidade em longas execuções
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--disable-remote-fonts")

        # pageLoadStrategy = "eager" retorna após DOMContentLoaded (mais rápido)
        chrome_options.set_capability("pageLoadStrategy", "eager")

        _DRIVER = webdriver.Chrome(options=chrome_options)

        # Define timeout de página (aumentado para 120s para páginas profundas)
        try:
            _DRIVER.set_page_load_timeout(120)
        except Exception:
            pass

        _PAGES_COUNT = 0

    return _DRIVER


def close_scraper():
    """Fecha o driver Selenium e limpa variáveis"""
    global _DRIVER
    if _DRIVER is not None:
        try:
            _DRIVER.quit()
        except Exception:
            pass
        _DRIVER = None


def handle_pagination(soup):
    """Extrai o total de páginas do HTML"""
    total_pages = 1
    title_tag = soup.find("h2", class_="q-page-results-title")
    if title_tag:
        text = title_tag.get_text(strip=False)
        match = re.search(r"encontradas\s+([\d.]+)\s+provas", text)
        if match:
            try:
                num_provas_str = match.group(1).replace(".", "")
                num_provas = int(num_provas_str)
                total_pages = (num_provas // 20) + (1 if num_provas % 20 != 0 else 0)
            except ValueError:
                pass
    return total_pages


def get_tests_from_page(page_url, page_number, scraper_config, max_retries=3):
    """
    Extrai testes de uma página com retry logic, fallback e rotação de navegador.
    """
    global _PAGES_COUNT

    # Incrementa contador de páginas processadas por este driver
    _PAGES_COUNT += 1

    # Reinicia o navegador se atingir o limite para limpar memória e evitar bloqueios
    if _PAGES_COUNT > MAX_PAGES_BEFORE_RESTART:
        print(
            f"♻ Reiniciando navegador (página {page_number}) para manter performance..."
        )
        create_scraper(force_restart=True)

    driver = create_scraper()
    last_exc = None
    soup = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"  → Tentativa {attempt}/{max_retries} para página {page_number}...")
            driver.get(page_url)

            # Aguarda elementos da página carregar (timeout de 20s para o elemento)
            WebDriverWait(driver, 20).until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, "q-exam-item"))
            )

            # Pequeno delay para garantir renderização completa
            time.sleep(2)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            break  # Sucesso

        except Exception as e:
            last_exc = e
            print(f"  ⚠ Falha na tentativa {attempt}: {type(e).__name__}")

            # Tenta parar o carregamento e parsear o que já chegou
            try:
                driver.execute_script("window.stop();")
                time.sleep(1)
                soup = BeautifulSoup(driver.page_source, "html.parser")

                # Verifica se conseguiu extrair algum conteúdo
                if soup.select(".q-exam-item"):
                    print(f"  ✓ Carregamento parcial aceito para página {page_number}")
                    break
            except Exception:
                pass

            # Backoff proporcional ao número da página (páginas mais profundas precisam de mais descanso)
            if attempt < max_retries:
                wait_time = (3 * attempt) + (page_number // 100)
                print(f"  ⏳ Aguardando {wait_time}s antes da próxima tentativa...")
                time.sleep(wait_time)
            else:
                # Última tentativa falhou - registra erro e continua
                error_msg = f"{type(last_exc).__name__}: {str(last_exc)[:200]}"
                bancas_nomes = [
                    b.get("nome", "") for b in scraper_config.get("bancas", [])
                ]
                anos = scraper_config.get("anos", [])
                log_error(page_number, bancas_nomes, anos, error_msg)
                print(
                    f"  ✗ Página {page_number} falhou após {max_retries} tentativas. Erro registrado em log."
                )
                return []

    # Extrai testes da página
    tests = []
    if soup is None:
        return []

    try:
        for item in soup.select(".q-exam-item"):
            test = {}

            title_span = item.select_one(".q-title")
            if title_span:
                title_parts = [
                    part.strip() for part in re.split(r" [-–] ", title_span.get_text())
                ]
                title_info = parse_title_parts(title_parts)
                test.update(title_info)

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

            if test:
                tests.append(test)
    except Exception as e:
        print(f"  ⚠ Erro ao parsear página {page_number}: {e}")

    # Delay adaptativo: aumenta o delay base conforme as páginas ficam mais profundas
    delay = 1.5 + (page_number // 100)
    time.sleep(delay)
    return tests


def scrape_tests(main_url: str, scraper_config: dict):
    """
    Faz o scraping de todas as páginas.
    Registra erros em log para páginas que falham.
    Ao final, registra sucesso com estatísticas.
    """
    driver = create_scraper()

    # Monta query parameters usando os códigos das bancas
    query_params = []
    for board in scraper_config.get("bancas", []):
        query_params.append(f"by_examining_board[]={board.get('codigo', '')}")
    for year in scraper_config.get("anos", []):
        query_params.append(f"application_year[]={year}")

    query_string = "&".join(query_params)
    page_url = f"{main_url}?{query_string}"

    # Tenta acessar a página inicial para pegar o total de páginas
    try:
        driver.get(page_url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "q-page-results-title"))
        )
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, "html.parser")
    except Exception as e:
        raise RuntimeError(f"Erro ao acessar URL inicial {main_url}: {e}")

    total_pages = handle_pagination(soup)

    # Extrai informações para o log e prints (usando nomes das bancas)
    bancas_nomes = [b.get("nome", "") for b in scraper_config.get("bancas", [])]
    anos = scraper_config.get("anos", [])

    print(f"\n{'=' * 60}")
    print(f"Total de páginas a raspar: {total_pages}")
    print(f"Bancas: {bancas_nomes}")
    print(f"Anos: {anos}")
    print(f"{'=' * 60}\n")

    tests = []
    for page in range(1, total_pages + 1):
        query_params_pagination = query_params + [f"page={page}"]
        query_string_pagination = "&".join(query_params_pagination)
        page_url = f"{main_url}?{query_string_pagination}"

        tests_count_before = len(tests)

        print(f"Página {page}/{total_pages}...")
        page_tests = get_tests_from_page(page_url, page, scraper_config, max_retries=3)
        tests.extend(page_tests)

        tests_extracted = len(tests) - tests_count_before
        print(f"✓ {tests_extracted} prova(s) extraída(s) | Total: {len(tests)}\n")

    print(f"\n{'=' * 60}")
    print("✓ RASPAGEM CONCLUÍDA!")
    print(f"Total de provas extraídas: {len(tests)}")
    print(f"{'=' * 60}\n")

    return tests
