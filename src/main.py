"""
main.py - Script de execução principal do PCI Concursos Harvester.

Este arquivo coordena:
- Inicialização do sistema
- Configuração de logging
- Definição de bancas a processar
- Execução do scraping
- Exportação de resultados
- Logging de bancas processadas
- Exibição de relatórios
"""

import logging
import logging.handlers
import sys
import time
from pathlib import Path

# Adicionar src ao path para importar módulos
sys.path.insert(0, str(Path(__file__).parent))

from exporters import (
    export_tests_to_csv,
    log_bancas_processadas,
    save_error_report,
    save_statistics_report,
)
from performance import MAX_RETRIES, THREAD_POOL_SIZE, TIMEOUT
from scraper import get_roles, stats

# ============================================================================
# CONFIGURAÇÃO DE BANCAS
# ============================================================================

# Lista de bancas a processar
BANCAS_LISTA = [
    "fgv",
]

# ============================================================================
# CONFIGURAÇÃO DE EXPORTAÇÃO
# ============================================================================

CSV_OUTPUT = "provas.csv"
OUT_DIR = Path("out")

# ============================================================================
# CONFIGURAÇÃO DE LOGGING
# ============================================================================

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("pci_harvester")
logger.setLevel(logging.DEBUG)

log_format = logging.Formatter(
    "[%(asctime)s] [%(levelname)-8s] [%(threadName)-15s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Handler para arquivo INFO
info_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "pci_harvester.log",
    maxBytes=50 * 1024 * 1024,
    backupCount=10,
)
info_handler.setLevel(logging.INFO)
info_handler.setFormatter(log_format)

# Handler para arquivo ERROR
error_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "pci_harvester_errors.log",
    maxBytes=50 * 1024 * 1024,
    backupCount=10,
)
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(log_format)

# Handler para console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(log_format)

logger.addHandler(info_handler)
logger.addHandler(error_handler)
logger.addHandler(console_handler)


# ============================================================================
# FUNÇÃO MAIN
# ============================================================================


def main():
    """
    Função principal de execução.

    Orquestração do processo:
    1. Log de inicialização
    2. Validação de configurações
    3. Execução do scraping
    4. Exportação de dados
    5. Logging de bancas processadas
    6. Geração de relatórios
    7. Exibição de estatísticas
    """
    try:
        logger.info("=" * 100)
        logger.info("PCI CONCURSOS - HARVESTER DE PROVAS")
        logger.info("=" * 100)

        # ====================================================================
        # VALIDAÇÃO DE CONFIGURAÇÕES
        # ====================================================================

        logger.info("Validando configurações...")

        if not BANCAS_LISTA:
            logger.error("ERRO: Lista de bancas vazia. Abortando.")
            return 1

        main_url = "https://www.pciconcursos.com.br/provas"

        logger.info(f"✓ URL Principal: {main_url}")
        logger.info(f"✓ Bancas a processar: {len(BANCAS_LISTA)}")
        logger.info(f"✓ Threads: {THREAD_POOL_SIZE}")
        logger.info(f"✓ Retries: {MAX_RETRIES}")
        logger.info(f"✓ Timeout: {TIMEOUT}s")
        logger.info("")

        # ====================================================================
        # EXECUÇÃO DO SCRAPING
        # ====================================================================

        logger.info("Iniciando coleta de provas...")
        start_time = time.time()

        tests = get_roles(main_url, BANCAS_LISTA)

        elapsed_time = time.time() - start_time

        if not tests:
            logger.warning("AVISO: Nenhuma prova foi coletada")
            return 1

        logger.info(
            f"\n✓ Coleta finalizada: {len(tests)} provas em {elapsed_time:.2f}s"
        )

        # ====================================================================
        # EXPORTAÇÃO DE DADOS
        # ====================================================================

        logger.info("\nExportando dados...")

        # Garantir que o diretório /out existe
        OUT_DIR.mkdir(parents=True, exist_ok=True)

        # Exportar CSV com append (não sobrescreve dados anteriores)
        success_csv = export_tests_to_csv(tests, CSV_OUTPUT)

        if not success_csv:
            logger.error("Erro ao exportar CSV. Abortando.")
            return 1

        # ====================================================================
        # LOGGING DE BANCAS PROCESSADAS
        # ====================================================================

        logger.info("\nRegistrando bancas processadas...")

        # Registrar todas as bancas com estatísticas completas
        success_log = log_bancas_processadas(BANCAS_LISTA, stats, elapsed_time)

        if not success_log:
            logger.warning("Aviso ao registrar bancas no log")

        # ====================================================================
        # GERAÇÃO DE RELATÓRIOS
        # ====================================================================

        logger.info("\nGerando relatórios...")

        # Salvar relatório de erros
        save_error_report(stats, "error_report.json")

        # Salvar relatório de estatísticas
        save_statistics_report(stats, elapsed_time, "statistics.json")

        # ====================================================================
        # EXIBIÇÃO DE ESTATÍSTICAS FINAIS
        # ====================================================================

        stats["end_time"] = time.time()

        logger.info("")
        logger.info("=" * 100)
        logger.info("RESUMO FINAL")
        logger.info("=" * 100)

        logger.info(
            f"Tempo total: {elapsed_time:.2f}s ({elapsed_time / 60:.2f} minutos)"
        )
        logger.info(f"Bancas processadas: {stats['roles_processed']}")
        logger.info(f"Páginas processadas: {stats['pages_processed']}")
        logger.info(f"URLs coletadas: {stats['total_urls_collected']}")
        logger.info(f"Provas com sucesso: {stats['successful_tests']}")
        logger.info(f"Provas com erro: {stats['failed_tests']}")
        logger.info(f"Total exportado: {len(tests)}")

        if stats["total_urls_collected"] > 0:
            taxa_sucesso = (
                stats["successful_tests"]
                / (stats["successful_tests"] + stats["failed_tests"])
                * 100
            )
            logger.info(f"Taxa de sucesso: {taxa_sucesso:.2f}%")

        if elapsed_time > 0:
            velocidade = len(tests) / elapsed_time
            logger.info(f"Velocidade média: {velocidade:.2f} provas/segundo")

        if stats["failed_urls"]:
            logger.warning(f"\nURLs com erro ({len(stats['failed_urls'])}):")
            for url, error in stats["failed_urls"][:5]:
                logger.warning(f"  - {url}: {error}")

        logger.info("")
        logger.info(f"Arquivo de saída (CSV): out/{CSV_OUTPUT}")
        logger.info("Log de bancas: out/bancas.log.txt")
        logger.info("Relatórios: out/")
        logger.info(f"Logs detalhados: {LOG_DIR}/")
        logger.info("=" * 100)

        return 0

    except KeyboardInterrupt:
        logger.warning("\n\nColeta interrompida pelo usuário (Ctrl+C)")
        return 130

    except Exception as e:
        logger.critical(f"ERRO CRÍTICO: {str(e)}", exc_info=True)
        return 1


# ============================================================================
# PONTO DE ENTRADA
# ============================================================================

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
