import logging
import logging.handlers
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock, Semaphore

import cloudscraper
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, RequestException, Timeout
from urllib3.util.retry import Retry

# ============================================================================
# CONFIGURAÇÃO DE LOGGING PROFISSIONAL
# ============================================================================

# Criar diretório de logs se não existir
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Configurar logger raiz
logger = logging.getLogger("pci_harvester")
logger.setLevel(logging.DEBUG)

# Formato detalhado com timestamp
log_format = logging.Formatter(
    "[%(asctime)s] [%(levelname)-8s] [%(threadName)-15s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Handler para arquivo de INFO (progresso geral)
info_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "pci_harvester.log",
    maxBytes=50 * 1024 * 1024,  # 50 MB
    backupCount=10,
)
info_handler.setLevel(logging.INFO)
info_handler.setFormatter(log_format)

# Handler para arquivo de ERRORS (registra todos os erros)
error_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "pci_harvester_errors.log",
    maxBytes=50 * 1024 * 1024,  # 50 MB
    backupCount=10,
)
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(log_format)

# Handler para console (progress em tempo real)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(log_format)

# Adicionar handlers ao logger
logger.addHandler(info_handler)
logger.addHandler(error_handler)
logger.addHandler(console_handler)

# ============================================================================
# CONSTANTES E CONFIGURAÇÕES
# ============================================================================

MAX_RETRIES = 5
TIMEOUT = 30
BACKOFF_FACTOR = 2.0
THREAD_POOL_SIZE = 24  # 24 workers para processamento massivo
RATE_LIMIT_DELAY = 0.2  # Delay mínimo entre requisições
CONNECTION_POOL_SIZE = 100  # Pool grande para muitas conexões simultâneas
MAX_PAGES_PER_ROLE = 1000  # Limite de segurança para paginação infinita

# ============================================================================
# LOCKS E SINCRONIZAÇÃO
# ============================================================================

results_lock = Lock()
error_stats_lock = Lock()
rate_limiter = Semaphore(8)  # Máximo 8 requisições simultâneas

# Estatísticas globais (thread-safe)
stats = {
    "total_urls_collected": 0,
    "successful_tests": 0,
    "failed_tests": 0,
    "failed_urls": [],
    "roles_processed": 0,
    "pages_processed": 0,
    "start_time": None,
    "end_time": None,
}


# ============================================================================
# CRIAÇÃO DE SCRAPER COM RETRY STRATEGY
# ============================================================================


def create_resilient_scraper():
    """
    Cria uma sessão do cloudscraper otimizada para processamento em larga escala.

    Características:
    - Retry strategy com backoff exponencial
    - Connection pooling agressivo
    - Timeout configurável
    - Tratamento de erros 5XX e rate limiting (429)
    """
    scraper = cloudscraper.create_scraper()

    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[408, 429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=CONNECTION_POOL_SIZE,
        pool_maxsize=CONNECTION_POOL_SIZE,
    )

    scraper.mount("http://", adapter)
    scraper.mount("https://", adapter)

    return scraper


def rate_limited_get(scraper, url, timeout=TIMEOUT):
    """
    Requisição GET com rate limiting adaptativo e tratamento robusto.

    NUNCA levanta exceção não capturada:
    - Retorna response se sucesso
    - Retorna None se falha (após todas as tentativas)
    - Registra todos os erros em log
    """
    try:
        with rate_limiter:
            # Delay com jitter para não parecer ataque
            delay = RATE_LIMIT_DELAY + random.uniform(0, 0.3)
            time.sleep(delay)

            response = scraper.get(
                url, timeout=timeout, verify=True, allow_redirects=True
            )

            response.raise_for_status()
            return response

    except (Timeout, ConnectionError) as e:
        logger.debug(f"Timeout/Conexão em {url}: {type(e).__name__}")
        return None
    except RequestException as e:
        logger.debug(f"Erro de requisição em {url}: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado em {url}: {type(e).__name__}: {str(e)}")
        return None


# ============================================================================
# GET_ROLES - ITERA POR TODOS OS CARGOS
# ============================================================================


def get_roles(main_url):
    """
    Coleta todos os cargos da página principal.
    Itera por cada cargo e coleta todas as provas de forma paralela.

    Retorna lista de dicionários com dados das provas.
    """
    logger.info("=" * 90)
    logger.info("PCI CONCURSOS - HARVESTER DE PROVAS EM LARGA ESCALA")
    logger.info(f"URL Principal: {main_url}")
    logger.info(
        f"Configuração: {MAX_RETRIES} retries, {THREAD_POOL_SIZE} threads, "
        f"timeout={TIMEOUT}s, rate_limit={RATE_LIMIT_DELAY}s"
    )
    logger.info("=" * 90)

    stats["start_time"] = time.time()
    scraper = create_resilient_scraper()

    # Carregar página principal
    try:
        response = rate_limited_get(scraper, main_url)
        if response is None:
            logger.error(
                "FALHA CRÍTICA: Impossível carregar página principal após todas as tentativas"
            )
            return []

        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        logger.error(
            f"FALHA CRÍTICA ao processar página principal: {str(e)}", exc_info=True
        )
        return []

    # Coletar todos os cargos
    cargo_links = soup.select("#provas .link-i a")
    logger.info(f"Encontrados {len(cargo_links)} cargos para processar")

    if not cargo_links:
        logger.warning("AVISO: Nenhum cargo encontrado na página principal")
        return []

    all_tests = []

    # Processar cada cargo iterativamente
    for idx, link in enumerate(cargo_links, 1):
        try:
            href = link.get("href")
            cargo_name = link.get_text(strip=True)

            if not href:
                logger.warning(
                    f"[{idx}/{len(cargo_links)}] Link sem href encontrado: {cargo_name}"
                )
                continue

            logger.info(f"[{idx}/{len(cargo_links)}] Processando cargo: '{cargo_name}'")

            # Processar cargo e coletar provas
            role_tests = get_exams(href, cargo_name)

            with results_lock:
                all_tests.extend(role_tests)
                with error_stats_lock:
                    stats["roles_processed"] += 1

            logger.info(
                f"[{idx}/{len(cargo_links)}] ✓ Cargo '{cargo_name}' concluído: "
                f"{len(role_tests)} provas coletadas"
            )

        except Exception as e:
            logger.error(
                f"[{idx}/{len(cargo_links)}] ERRO ao processar cargo: {str(e)}",
                exc_info=True,
            )
            # Continua com próximo cargo mesmo em caso de erro
            with error_stats_lock:
                stats["failed_urls"].append(
                    (link.get("href", "URL desconhecida"), str(e))
                )
            continue

    logger.info(
        f"Processamento de cargos finalizado: {stats['roles_processed']} de {len(cargo_links)} cargos"
    )
    return all_tests


# ============================================================================
# GET_EXAMS - COLETA PROVAS COM PAGINAÇÃO
# ============================================================================


def get_exams(role_url, cargo_name=""):
    """
    Coleta todas as provas de um cargo com paginação.

    Estratégia em 2 fases:
    1. Paginação sequencial para coletar URLs (evita race conditions)
    2. Processamento paralelo com ThreadPoolExecutor (máxima velocidade)
    """
    scraper = create_resilient_scraper()

    all_test_urls = []
    end_loop = False
    count = 1
    pages_processed = 0

    logger.debug(f"[PAGINAÇÃO] '{cargo_name}': Iniciando coleta de URLs")

    # ========================================================================
    # FASE 1: COLETA SEQUENCIAL DE URLs COM PAGINAÇÃO
    # ========================================================================

    while not end_loop and count <= MAX_PAGES_PER_ROLE:
        try:
            if count == 1:
                page_url = role_url
            else:
                page_url = role_url.rstrip("/") + f"/{count}"

            response = rate_limited_get(scraper, page_url)

            if response is None:
                logger.debug(
                    f"[PAGINAÇÃO] '{cargo_name}': Falha na página {count}, continuando..."
                )
                count += 1
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            pages_processed += 1

            with error_stats_lock:
                stats["pages_processed"] += 1

            # Verificar se chegou ao final da paginação
            page_empty = False
            for p_tag in soup.find_all("p"):
                try:
                    p_text = p_tag.get_text(strip=True)
                    if p_text and "nenhuma prova encontrada" in p_text.lower():
                        logger.debug(
                            f"[PAGINAÇÃO] '{cargo_name}': Fim detectado na página {count}"
                        )
                        page_empty = True
                        end_loop = True
                        break
                except Exception as e:
                    logger.debug(f"Erro ao processar parágrafo: {e}")
                    continue

            if end_loop:
                break

            # Coletar links de provas
            links = soup.select("a.prova_download")

            if not links:
                logger.debug(
                    f"[PAGINAÇÃO] '{cargo_name}': Nenhum link na página {count}"
                )
                end_loop = True
                break

            for link in links:
                href = link.get("href")
                if href:
                    all_test_urls.append(href)

            with error_stats_lock:
                stats["total_urls_collected"] += len(links)

            logger.debug(
                f"[PAGINAÇÃO] '{cargo_name}': Página {count} - {len(links)} provas coletadas"
            )

            count += 1

        except Exception as e:
            logger.error(
                f"[PAGINAÇÃO] '{cargo_name}': Erro na página {count}: {str(e)}"
            )
            count += 1
            if count > MAX_PAGES_PER_ROLE:
                logger.warning(
                    f"[PAGINAÇÃO] '{cargo_name}': Limite de páginas atingido ({MAX_PAGES_PER_ROLE})"
                )
                break

    logger.info(
        f"[PAGINAÇÃO] '{cargo_name}': {pages_processed} páginas, "
        f"{len(all_test_urls)} URLs de provas coletadas"
    )

    # ========================================================================
    # FASE 2: PROCESSAMENTO PARALELO DAS URLs
    # ========================================================================

    if not all_test_urls:
        logger.warning(f"[PARALELO] '{cargo_name}': Nenhuma URL para processar")
        return []

    return process_test_urls_parallel(all_test_urls, cargo_name)


# ============================================================================
# PROCESSAMENTO PARALELO
# ============================================================================


def process_test_urls_parallel(urls, cargo_name=""):
    """
    Processa URLs de provas em paralelo com ThreadPoolExecutor.

    - Usa 24 workers para máxima concorrência
    - Thread-safe com locks
    - NUNCA levanta exceção que interrompe o script
    - Registra progresso a cada 50 provas
    """
    if not urls:
        return []

    role_tests = []
    failed_in_batch = 0

    logger.info(
        f"[PARALELO] '{cargo_name}': Iniciando processamento de {len(urls)} provas"
    )

    try:
        with ThreadPoolExecutor(
            max_workers=THREAD_POOL_SIZE, thread_name_prefix=f"Worker"
        ) as executor:
            # Submeter todas as URLs para processamento
            futures = {executor.submit(get_test, url): url for url in urls}

            completed = 0
            for future in as_completed(futures):
                completed += 1
                url = futures[future]

                try:
                    test = future.result()

                    if test:
                        with results_lock:
                            role_tests.append(test)

                        with error_stats_lock:
                            stats["successful_tests"] += 1
                    else:
                        with error_stats_lock:
                            stats["failed_tests"] += 1
                        failed_in_batch += 1

                    # Log de progresso a cada 50 provas
                    if completed % 50 == 0 or completed == len(urls):
                        progress_pct = int(completed / len(urls) * 100)
                        logger.info(
                            f"[PARALELO] '{cargo_name}': {completed}/{len(urls)} "
                            f"processadas ({progress_pct}%) - {len(role_tests)} sucesso, "
                            f"{failed_in_batch} erro"
                        )

                except Exception as e:
                    logger.error(
                        f"[PARALELO] Erro ao processar URL: {str(e)}", exc_info=True
                    )
                    with error_stats_lock:
                        stats["failed_tests"] += 1
                        stats["failed_urls"].append((url, str(e)))
                    failed_in_batch += 1

    except Exception as e:
        logger.error(
            f"[PARALELO] '{cargo_name}': Erro crítico no executor: {str(e)}",
            exc_info=True,
        )

    logger.info(
        f"[PARALELO] '{cargo_name}': Concluído - {len(role_tests)} sucesso, "
        f"{failed_in_batch} falhas"
    )

    return role_tests


# ============================================================================
# GET_TEST - EXTRAÇÃO DE DADOS
# ============================================================================


def get_test(url):
    """
    Extrai informações de uma prova individual.
    Executada em thread separada.

    GARANTIAS:
    - NUNCA levanta exceção não capturada
    - Sempre retorna None ou dict
    - Registra todos os erros em log
    - Trata timeout e conexão de forma graceful
    """
    scraper = create_resilient_scraper()

    try:
        response = rate_limited_get(scraper, url)

        if response is None:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

    except Exception as e:
        logger.error(f"Erro ao carregar prova {url}: {str(e)}")
        return None

    test = {}

    try:
        # Extrair cargo, ano, órgão, banca
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

        # Extrair links de prova e gabarito (por posição)
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
                    if i == 0:
                        test["prova"] = href
                    elif i == 1:
                        test["gabarito"] = href

    except Exception as e:
        logger.error(f"Erro ao extrair dados de {url}: {str(e)}")
        # Continua mesmo com erro na extração

    return test if test else None


# ============================================================================
# EXPORTAÇÃO
# ============================================================================


def export_tests_to_csv(tests, filename="provas.csv"):
    """
    Exporta testes para CSV com encoding UTF-8.
    """
    import csv

    if not tests:
        logger.warning("Nenhum teste para exportar")
        return

    try:
        keys = tests[0].keys()
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tests)
        logger.info(f"✓ Exportação concluída: {len(tests)} provas em '{filename}'")
    except Exception as e:
        logger.error(f"Erro ao exportar CSV: {str(e)}", exc_info=True)


# ============================================================================
# MAIN
# ============================================================================


def main():
    """
    Função principal de execução.
    Garante que erros não interrompem o script.
    """
    try:
        main_url = "https://www.pciconcursos.com.br/provas"
        tests = get_roles(main_url)

        if tests:
            export_tests_to_csv(tests)

        # ====================================================================
        # ESTATÍSTICAS FINAIS
        # ====================================================================

        stats["end_time"] = time.time()
        elapsed = stats["end_time"] - stats["start_time"]

        logger.info("=" * 90)
        logger.info("COLETA FINALIZADA")
        logger.info("=" * 90)
        logger.info(f"Tempo total: {elapsed:.2f}s ({elapsed / 60:.2f} minutos)")
        logger.info(f"Cargos processados: {stats['roles_processed']}")
        logger.info(f"Páginas processadas: {stats['pages_processed']}")
        logger.info(f"URLs coletadas: {stats['total_urls_collected']}")
        logger.info(f"Provas com sucesso: {stats['successful_tests']}")
        logger.info(f"Provas com erro: {stats['failed_tests']}")
        logger.info(f"Total de provas exportadas: {len(tests)}")

        if stats["failed_urls"]:
            logger.warning(f"URLs com erro: {len(stats['failed_urls'])}")
            for url, error in stats["failed_urls"][:10]:  # Mostrar primeiras 10
                logger.warning(f"  - {url}: {error}")

        if len(tests) > 0:
            logger.info(f"Velocidade média: {len(tests) / elapsed:.2f} provas/segundo")

        logger.info("=" * 90)
        logger.info(f"Logs disponíveis em: {LOG_DIR}/")
        logger.info("=" * 90)

    except Exception as e:
        logger.critical(f"ERRO CRÍTICO na execução: {str(e)}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
