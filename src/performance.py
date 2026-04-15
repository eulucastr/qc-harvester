import logging
import random
import time
from threading import Lock, Semaphore

import cloudscraper
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, RequestException, Timeout
from urllib3.util.retry import Retry

# ============================================================================
# CONFIGURAÇÃO DE LOGGING
# ============================================================================

logger = logging.getLogger("pci_harvester")

# ============================================================================
# CONSTANTES DE PERFORMANCE
# ============================================================================

MAX_RETRIES = 5
TIMEOUT = 30
BACKOFF_FACTOR = 2.0
THREAD_POOL_SIZE = 24
RATE_LIMIT_DELAY = 0.2
CONNECTION_POOL_SIZE = 100
MAX_PAGES_PER_ROLE = 1000

# ============================================================================
# LOCKS E SINCRONIZAÇÃO
# ============================================================================

results_lock = Lock()
error_stats_lock = Lock()
rate_limiter = Semaphore(8)


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

    Args:
        scraper: Sessão do cloudscraper
        url: URL para fazer requisição
        timeout: Timeout em segundos

    Returns:
        response object ou None em caso de erro
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
