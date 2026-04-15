"""
scraper.py - Módulo de scraping principal.

Contém:
- Função get_roles() - itera por bancas
- Função get_exams() - coleta provas com paginação
- Função process_test_urls_parallel() - processa URLs em paralelo
- Função get_test() - extrai dados de uma prova
- Estatísticas e logging
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from performance import (
    MAX_PAGES_PER_ROLE,
    MAX_RETRIES,
    THREAD_POOL_SIZE,
    TIMEOUT,
    create_resilient_scraper,
    error_stats_lock,
    rate_limited_get,
    results_lock,
)

# ============================================================================
# CONFIGURAÇÃO DE LOGGING
# ============================================================================

logger = logging.getLogger("pci_harvester")

# ============================================================================
# ESTATÍSTICAS GLOBAIS
# ============================================================================

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
# GET_ROLES - ITERA POR BANCAS
# ============================================================================


def get_roles(main_url, bancas_lista):
    """
    Coleta provas por banca em vez de por cargo.

    Args:
        main_url (str): URL base, ex: "https://www.pciconcursos.com.br/provas"
        bancas_lista (list): Lista de bancas, ex: ['fgv', 'cebraspe', 'cesgranrio']

    Estratégia:
    1. Valida entrada
    2. Para cada banca na lista:
        - Constrói URL: main_url + "/" + banca
        - Coleta todas as provas daquela banca (com paginação)
        - Processa em paralelo com threads
    3. Retorna lista consolidada de todas as provas

    Estrutura de URL:
    - Primeira página:  https://www.pciconcursos.com.br/provas/fgv
    - Segunda página:   https://www.pciconcursos.com.br/provas/fgv/2
    - Terceira página:  https://www.pciconcursos.com.br/provas/fgv/3
    """
    logger.info("=" * 90)
    logger.info("PCI CONCURSOS - HARVESTER DE PROVAS POR BANCA")
    logger.info(f"URL Base: {main_url}")
    logger.info(f"Bancas a processar: {len(bancas_lista)}")
    logger.info(
        f"Configuração: {THREAD_POOL_SIZE} threads | {MAX_RETRIES} retries | timeout={TIMEOUT}s | max {MAX_PAGES_PER_ROLE} páginas"
    )
    logger.info("=" * 90)

    # Validação de entrada
    if not main_url:
        logger.error("ERRO: main_url não pode estar vazia")
        return []

    if not bancas_lista or len(bancas_lista) == 0:
        logger.error("ERRO: bancas_lista não pode estar vazia")
        return []

    if not isinstance(bancas_lista, list):
        logger.error(
            f"ERRO: bancas_lista deve ser uma lista, recebido {type(bancas_lista)}"
        )
        return []

    stats["start_time"] = time.time()

    logger.info(f"Iniciando coleta de {len(bancas_lista)} banca(s):")
    for banca in bancas_lista:
        logger.info(f"  - {banca}")

    all_tests = []

    # ========================================================================
    # PROCESSAR CADA BANCA
    # ========================================================================

    for idx, banca in enumerate(bancas_lista, 1):
        try:
            # Construir URL da banca
            banca_url = main_url.rstrip("/") + "/" + banca.strip()
            banca_name = banca.upper()

            logger.info(
                f"\n[{idx}/{len(bancas_lista)}] Processando banca: '{banca_name}' | URL: {banca_url}"
            )

            # Coleta de provas da banca (com paginação + threading)
            banca_tests = get_exams(banca_url, banca_name)

            with results_lock:
                all_tests.extend(banca_tests)
                with error_stats_lock:
                    stats["roles_processed"] += 1

            logger.info(
                f"[{idx}/{len(bancas_lista)}] ✓ Banca '{banca_name}' concluída: "
                f"{len(banca_tests)} provas coletadas | Total acumulado: {len(all_tests)}"
            )

        except Exception as e:
            logger.error(
                f"[{idx}/{len(bancas_lista)}] ERRO ao processar banca '{banca}': {str(e)}",
                exc_info=True,
            )
            with error_stats_lock:
                stats["failed_urls"].append((locals().get("banca_url", main_url.rstrip("/") + "/" + str(banca).strip()), str(e)))
            continue

    logger.info(
        f"\nProcessamento de bancas finalizado: {stats['roles_processed']} de {len(bancas_lista)} banca(s)"
    )
    logger.info(f"Total geral de provas coletadas: {len(all_tests)}")

    return all_tests


# ============================================================================
# GET_EXAMS - COLETA PROVAS COM PAGINAÇÃO
# ============================================================================


def get_exams(role_url, cargo_name=""):
    """
    Coleta todas as provas de uma banca com paginação.

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
            for p_tag in soup.find_all("p"):
                try:
                    p_text = p_tag.get_text(strip=True)
                    if p_text and "nenhuma prova encontrada" in p_text.lower():
                        logger.debug(
                            f"[PAGINAÇÃO] '{cargo_name}': Fim detectado na página {count}"
                        )
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

    - Usa múltiplos workers para máxima concorrência
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
            max_workers=THREAD_POOL_SIZE, thread_name_prefix="Worker"
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

    Extrai:
    - Cargo
    - Ano
    - Entidade/Órgão
    - Banca/Organizadora
    - Link da prova (PDF) - posição 0
    - Link do gabarito (PDF) - posição 1
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
