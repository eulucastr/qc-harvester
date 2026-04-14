import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore

import cloudscraper
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================================
# CONFIGURAÇÃO DE LOGGING
# ============================================================================
logging.basicConfig(
    level=logging.INFO, format="[%(levelname)s - %(threadName)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTES E CONFIGURAÇÕES
# ============================================================================
MAX_RETRIES = 3
TIMEOUT = 20
BACKOFF_FACTOR = 1.5
THREAD_POOL_SIZE = 4  # Workers de threads (não muito alto para não parecer ataque)
RATE_LIMIT_DELAY = 0.5  # Delay entre requisições em segundos
STATUS_FORCELIST = [429, 500, 502, 503, 504]  # Status codes para retry

# ============================================================================
# LOCKS E SINCRONIZAÇÃO
# ============================================================================
results_lock = Lock()
rate_limiter = Semaphore(1)  # Controla acesso a rate limit


def _create_retry_session():
    """
    Cria uma sessão do cloudscraper com retry strategy robusto.
    Implementa backoff exponencial e retry automático para falhas de conexão.

    Strategy:
    - 3 tentativas totais
    - Backoff exponencial: 1.5x entre tentativas
    - Retry em status codes 429, 500, 502, 503, 504
    - Timeout: 20 segundos
    """
    scraper = cloudscraper.create_scraper()

    # Configurar retry strategy com backoff exponencial
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=STATUS_FORCELIST,
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        raise_on_status=False,  # Não lance exceção, apenas retorne o status
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    scraper.mount("http://", adapter)
    scraper.mount("https://", adapter)

    return scraper


def _rate_limited_get(scraper, url, timeout=TIMEOUT):
    """
    Faz requisição GET com rate limiting e tratamento robusto de erros.

    Caracterísitcas:
    - Rate limiting para não sobrecarregar servidor
    - Delay aleatório para parecer mais natural
    - Retry automático com backoff exponencial
    - Log detalhado de sucesso/erro
    - Tratamento de timeouts e erros de conexão
    """
    with rate_limiter:
        # Delay randômico para parecer mais natural
        delay = RATE_LIMIT_DELAY + random.uniform(0, 0.5)
        time.sleep(delay)

        try:
            logger.info(f"Acessando: {url}")
            response = scraper.get(url, timeout=timeout)
            response.raise_for_status()
            logger.info(f"✓ Sucesso (Status {response.status_code}): {url}")
            return response
        except Exception as e:
            logger.error(
                f"✗ Erro após {MAX_RETRIES} tentativas em {url}: {type(e).__name__}: {str(e)}"
            )
            raise


# ============================================================================
# FUNÇÃO: GET_ROLES
# ============================================================================
def get_roles(main_url):
    """
    Resgata os cargos da página principal e inicia coleta de provas.
    """
    scraper = _create_retry_session()
    try:
        response = _rate_limited_get(scraper, main_url)
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        logger.error(f"Falha ao carregar página principal: {e}")
        return []

    tests = []
    # Hardcoded por enquanto, mas pode ser iterado se necessário
    for link in soup.select("#provas .link-i a"):
        href = link.get("href")
        role_tests = get_exams(href)
        tests.extend(role_tests)

    return tests


# ============================================================================
# FUNÇÃO: GET_EXAMS (Com paginação e threading)
# ============================================================================
def get_exams(role_url):
    """
    Resgata links de provas com paginação.

    Estratégia em 2 fases:
    1. Coleta sequencial de URLs de provas (com paginação)
    2. Processamento paralelo com ThreadPoolExecutor

    Isso melhora performance significativamente pois:
    - Coleta não é paralelizada (mantém paginação sequencial)
    - Processamento individual de cada prova é paralelizado
    """
    scraper = _create_retry_session()

    all_test_urls = []
    end_loop = False
    count = 1

    # Fase 1: Coletar todas as URLs de provas (com paginação sequencial)
    logger.info(f"Fase 1 - Coleta de URLs: {role_url}")
    while not end_loop:
        if count == 1:
            page_url = role_url
        else:
            page_url = role_url.rstrip("/") + f"/{count}"

        try:
            response = _rate_limited_get(scraper, page_url)
            soup = BeautifulSoup(response.text, "html.parser")

            logger.info(f"Processando página {count}: {page_url}")

            # Verificar se "Nenhuma prova encontrada"
            found_empty = False
            for p_tag in soup.find_all("p"):
                p_text = p_tag.get_text(strip=True)
                if p_text and "nenhuma prova encontrada" in p_text.lower():
                    logger.info(
                        f"'Nenhuma prova encontrada' na página {count}. Encerrando paginação."
                    )
                    found_empty = True
                    end_loop = True
                    break

            if end_loop:
                break

            # Coletar links de provas
            links = soup.select("a.prova_download")
            if not links:
                logger.warning(f"Nenhum link de prova encontrado na página {count}")
                end_loop = True
                break

            logger.info(f"Encontrados {len(links)} provas na página {count}")
            for link in links:
                href = link.get("href")
                if href:
                    all_test_urls.append(href)

            count += 1

        except Exception as e:
            logger.error(f"Erro ao processar página {count}: {e}")
            end_loop = True

    # Fase 2: Processar URLs de provas com ThreadPoolExecutor (paralelo)
    logger.info(
        f"Fase 2 - Processamento paralelo: {len(all_test_urls)} provas com {THREAD_POOL_SIZE} threads"
    )

    role_tests = []
    with ThreadPoolExecutor(
        max_workers=THREAD_POOL_SIZE, thread_name_prefix="Worker"
    ) as executor:
        # Enviar todas as URLs para processamento
        futures = {executor.submit(get_test, url): url for url in all_test_urls}

        # Processar resultados conforme completam
        completed = 0
        for future in as_completed(futures):
            completed += 1
            url = futures[future]
            try:
                test = future.result()
                if test:
                    with results_lock:
                        role_tests.append(test)
                    logger.debug(
                        f"[{completed}/{len(all_test_urls)}] Prova processada: {test.get('cargo', 'N/A')}"
                    )
            except Exception as e:
                logger.error(f"Erro ao processar {url}: {e}")

    logger.info(f"Fase 2 - Finalizado. Total de provas: {len(role_tests)}")
    return role_tests


# ============================================================================
# FUNÇÃO: GET_TEST
# ============================================================================
def get_test(url):
    """
    Extrai informações de uma prova individual.
    Executada em thread separada com retry strategy própria.

    Extrai:
    - Cargo
    - Ano
    - Entidade/Órgão
    - Banca/Organizadora
    - Link da prova (PDF)
    - Link do gabarito (PDF)
    """
    scraper = _create_retry_session()

    try:
        response = _rate_limited_get(scraper, url)
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        logger.error(f"Falha ao carregar prova {url}: {e}")
        return None

    test = {}

    try:
        # Extrair cargo, ano, órgão, banca iterando pelas <li>
        list_items = soup.select("#download .card-body ul li")
        for li in list_items:
            text = li.get_text(strip=True)

            if "Cargo:" in text:
                test["cargo"] = text.replace("Cargo:", "").strip()
            elif "Ano:" in text:
                test["ano"] = text.replace("Ano:", "").strip()
            elif "Órgão:" in text:
                test["entidade"] = text.replace("Órgão:", "").strip()
            elif "Organizadora:" in text:
                test["banca"] = text.replace("Organizadora:", "").strip()

        # Extrair links de prova e gabarito
        download_card = None
        for card in soup.select("#download .card"):
            h5 = card.select_one(".card-header h5")
            if h5 and "Download" in h5.get_text():
                download_card = card
                break

        if download_card:
            download_links = download_card.select(".item-link")
            for i, link in enumerate(download_links):
                href = link.get("href")
                if href:
                    # Primeiro link é sempre a prova, segundo é o gabarito
                    if i == 0:
                        test["prova"] = href
                    elif i == 1:
                        test["gabarito"] = href

    except Exception as e:
        logger.error(f"Erro ao extrair dados da prova {url}: {e}")

    return test if test else None


# ============================================================================
# FUNÇÃO: EXPORT_TESTS_TO_CSV
# ============================================================================
def export_tests_to_csv(tests, filename="provas.csv"):
    """
    Exporta testes para CSV com tratamento de encoding.
    """
    import csv

    if not tests:
        logger.warning("Nenhum teste para exportar.")
        return

    try:
        keys = tests[0].keys()
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tests)
        logger.info(f"✓ {len(tests)} provas exportadas para '{filename}'")
    except Exception as e:
        logger.error(f"Erro ao exportar CSV: {e}")


# ============================================================================
# FUNÇÃO MAIN
# ============================================================================
def main():
    """
    Função principal de execução.
    """
    logger.info("=" * 80)
    logger.info("Iniciando coleta de provas - PCI Concursos")
    logger.info(
        f"Configuração: {MAX_RETRIES} retries, {THREAD_POOL_SIZE} threads, timeout={TIMEOUT}s"
    )
    logger.info("=" * 80)

    start_time = time.time()

    main_url = "https://www.pciconcursos.com.br/provas"
    tests = get_roles(main_url)

    if tests:
        export_tests_to_csv(tests)

    elapsed_time = time.time() - start_time
    logger.info("=" * 80)
    logger.info(f"Coleta finalizada em {elapsed_time:.2f}s")
    logger.info(f"Total de provas coletadas: {len(tests)}")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
