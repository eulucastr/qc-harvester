"""
exporters.py - Módulo de exportação de dados.

Contém:
- Exportação para CSV
- Geração de relatório de erros em JSON
- Funções de formatação e validação
"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("pci_harvester")


def export_tests_to_csv(tests, filename="provas.csv"):
    """
    Exporta testes para CSV com tratamento robusto de encoding.

    Args:
        tests (list): Lista de dicionários com dados das provas
        filename (str): Nome do arquivo de saída

    Returns:
        bool: True se sucesso, False se erro
    """
    if not tests:
        logger.warning("Nenhum teste para exportar")
        return False

    try:
        keys = tests[0].keys()

        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tests)

        logger.info(f"✓ Exportação concluída: {len(tests)} provas em '{filename}'")
        return True

    except Exception as e:
        logger.error(f"Erro ao exportar CSV: {str(e)}", exc_info=True)
        return False


def save_error_report(stats, filename="logs/error_report.json"):
    """
    Salva relatório detalhado de erros em JSON.

    Args:
        stats (dict): Dicionário com estatísticas
        filename (str): Nome do arquivo de saída

    Returns:
        bool: True se sucesso, False se erro
    """
    try:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        report = {
            "timestamp": datetime.now().isoformat(),
            "resumo": {
                "total_urls_coletadas": stats.get("total_urls_collected", 0),
                "total_provas_processadas": stats.get("successful_tests", 0),
                "total_provas_com_erro": stats.get("failed_tests", 0),
                "total_erros": stats.get("successful_tests", 0)
                + stats.get("failed_tests", 0),
            },
            "detalhes_erros": {
                "urls_com_erro": len(stats.get("failed_urls", [])),
                "primeiras_urls_com_erro": [
                    {"url": url, "erro": error}
                    for url, error in stats.get("failed_urls", [])[:20]
                ],
            },
        }

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Relatório de erros salvo em '{filename}'")
        return True

    except Exception as e:
        logger.error(f"Erro ao salvar relatório: {str(e)}", exc_info=True)
        return False


def export_tests_to_json(tests, filename="provas.json"):
    """
    Exporta testes para JSON.

    Args:
        tests (list): Lista de dicionários com dados das provas
        filename (str): Nome do arquivo de saída

    Returns:
        bool: True se sucesso, False se erro
    """
    if not tests:
        logger.warning("Nenhum teste para exportar para JSON")
        return False

    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(tests, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Exportação JSON concluída: {len(tests)} provas em '{filename}'")
        return True

    except Exception as e:
        logger.error(f"Erro ao exportar JSON: {str(e)}", exc_info=True)
        return False


def save_statistics_report(stats, elapsed_time, filename="logs/statistics.json"):
    """
    Salva relatório completo de estatísticas.

    Args:
        stats (dict): Dicionário com estatísticas
        elapsed_time (float): Tempo total de execução em segundos
        filename (str): Nome do arquivo de saída

    Returns:
        bool: True se sucesso, False se erro
    """
    try:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        report = {
            "timestamp": datetime.now().isoformat(),
            "tempo_execucao": {
                "segundos": round(elapsed_time, 2),
                "minutos": round(elapsed_time / 60, 2),
                "horas": round(elapsed_time / 3600, 2),
            },
            "estatisticas_coleta": {
                "bancas_processadas": stats.get("roles_processed", 0),
                "paginas_processadas": stats.get("pages_processed", 0),
                "urls_coletadas": stats.get("total_urls_collected", 0),
                "provas_sucesso": stats.get("successful_tests", 0),
                "provas_erro": stats.get("failed_tests", 0),
                "taxa_sucesso": round(
                    (
                        stats.get("successful_tests", 0)
                        / (
                            stats.get("successful_tests", 0)
                            + stats.get("failed_tests", 1)
                        )
                    )
                    * 100,
                    2,
                )
                if (stats.get("successful_tests", 0) + stats.get("failed_tests", 0)) > 0
                else 0,
            },
            "performance": {
                "provas_por_segundo": round(
                    stats.get("successful_tests", 0) / elapsed_time, 2
                )
                if elapsed_time > 0
                else 0,
                "urls_por_segundo": round(
                    stats.get("total_urls_collected", 0) / elapsed_time, 2
                )
                if elapsed_time > 0
                else 0,
            },
        }

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Relatório de estatísticas salvo em '{filename}'")
        return True

    except Exception as e:
        logger.error(
            f"Erro ao salvar relatório de estatísticas: {str(e)}", exc_info=True
        )
        return False
