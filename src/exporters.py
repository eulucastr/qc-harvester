"""
exporters.py - Módulo de exportação de dados.

Contém:
- Exportação para CSV com append (não sobrescreve dados existentes)
- Logging de bancas processadas com estatísticas completas
- Geração de relatório de erros em JSON
- Funções de formatação e validação
"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("pci_harvester")

# ============================================================================
# CONFIGURAÇÃO DE CAMINHOS
# ============================================================================

OUT_DIR = Path("out")
OUT_DIR.mkdir(exist_ok=True)


# ============================================================================
# EXPORTAÇÃO PARA CSV COM APPEND
# ============================================================================


def export_tests_to_csv(tests, filename="provas.csv"):
    """
    Exporta testes para CSV, acrescentando aos dados existentes (não sobrescreve).

    Estratégia:
    1. Se arquivo existe: ler registros existentes
    2. Adicionar novos testes à lista
    3. Remover duplicatas (por URL ou identificador único)
    4. Escrever de volta ao arquivo

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
        # Construir caminho completo na pasta /out
        csv_path = OUT_DIR / filename

        # Ler registros existentes se o arquivo já existe
        existing_tests = []
        existing_urls = set()

        if csv_path.exists():
            try:
                with open(csv_path, "r", encoding="utf-8") as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        existing_tests.append(row)
                        # Usar URL como identificador único para evitar duplicatas
                        if "prova" in row:
                            existing_urls.add(row["prova"])

                logger.info(
                    f"✓ Arquivo CSV existente carregado: {len(existing_tests)} registros prévios"
                )
            except Exception as e:
                logger.warning(f"Aviso ao ler CSV existente: {str(e)}")
                existing_tests = []
                existing_urls = set()

        # Adicionar novos testes que não são duplicatas
        merged_tests = existing_tests.copy()
        new_tests_added = 0

        for test in tests:
            # Usar a URL da prova como chave única
            test_url = test.get("prova", "")
            if test_url and test_url not in existing_urls:
                merged_tests.append(test)
                existing_urls.add(test_url)
                new_tests_added += 1
            elif not test_url:
                # Se não tem URL, adicionar mesmo assim
                merged_tests.append(test)
                new_tests_added += 1

        # Escrever todos os registros de volta ao arquivo
        if merged_tests:
            keys = merged_tests[0].keys()

            with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=keys, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(merged_tests)

            logger.info(
                f"✓ Exportação concluída: {new_tests_added} novas provas adicionadas "
                f"(total: {len(merged_tests)}) em '{csv_path}'"
            )
        else:
            logger.warning("Nenhum teste para exportar após processamento")
            return False

        return True

    except Exception as e:
        logger.error(f"Erro ao exportar CSV: {str(e)}", exc_info=True)
        return False


# ============================================================================
# LOGGING DE BANCAS PROCESSADAS
# ============================================================================


def log_bancas_processadas(bancas_lista, stats, elapsed_time):
    """
    Registra no arquivo bancas.log.txt todas as bancas processadas com detalhes.

    Estrutura do log:
    - Data/hora da execução
    - Lista de bancas processadas
    - Estatísticas gerais
    - Detalhes de cada banca processada
    - Taxa de sucesso e performance

    Args:
        bancas_lista (list): Lista de bancas processadas
        stats (dict): Dicionário com estatísticas da execução
        elapsed_time (float): Tempo total de execução em segundos

    Returns:
        bool: True se sucesso, False se erro
    """
    try:
        log_path = OUT_DIR / "bancas.log.txt"

        # Preparar conteúdo do log
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        log_content = []
        log_content.append("=" * 100)
        log_content.append("REGISTRO DE PROCESSAMENTO DE BANCAS - PCI HARVESTER")
        log_content.append("=" * 100)
        log_content.append(f"Data/Hora: {timestamp}")
        log_content.append("")

        # Informações gerais
        log_content.append("INFORMAÇÕES GERAIS")
        log_content.append("-" * 100)
        log_content.append(f"Bancas processadas: {len(bancas_lista)}")
        log_content.append(
            f"Total de provas coletadas: {stats.get('total_urls_collected', 0)}"
        )
        log_content.append(f"Provas com sucesso: {stats.get('successful_tests', 0)}")
        log_content.append(f"Provas com erro: {stats.get('failed_tests', 0)}")

        total_processed = stats.get("successful_tests", 0) + stats.get(
            "failed_tests", 0
        )
        if total_processed > 0:
            taxa_sucesso = (stats.get("successful_tests", 0) / total_processed) * 100
            log_content.append(f"Taxa de sucesso: {taxa_sucesso:.2f}%")

        log_content.append(
            f"Tempo total de execução: {elapsed_time:.2f}s ({elapsed_time / 60:.2f} minutos)"
        )

        if elapsed_time > 0:
            velocidade = stats.get("successful_tests", 0) / elapsed_time
            log_content.append(f"Velocidade média: {velocidade:.2f} provas/segundo")

        log_content.append("")

        # Detalhes de cada banca
        log_content.append("BANCAS PROCESSADAS")
        log_content.append("-" * 100)
        for idx, banca in enumerate(bancas_lista, 1):
            log_content.append(f"{idx}. {banca.upper()}")

        log_content.append("")

        # Estatísticas detalhadas
        log_content.append("ESTATÍSTICAS DETALHADAS")
        log_content.append("-" * 100)
        log_content.append(f"Páginas processadas: {stats.get('pages_processed', 0)}")
        log_content.append(f"URLs coletadas: {stats.get('total_urls_collected', 0)}")
        log_content.append(
            f"Provas exportadas com sucesso: {stats.get('successful_tests', 0)}"
        )
        log_content.append(f"Provas com falha: {stats.get('failed_tests', 0)}")
        log_content.append(
            f"Roles/Bancas processadas: {stats.get('roles_processed', 0)}"
        )

        log_content.append("")

        # URLs com erro (se houver)
        if stats.get("failed_urls"):
            log_content.append("URLs COM ERRO (primeiras 10)")
            log_content.append("-" * 100)
            for url, error in stats.get("failed_urls", [])[:10]:
                log_content.append(f"URL: {url}")
                log_content.append(f"Erro: {error}")
                log_content.append("")

        log_content.append("=" * 100)
        log_content.append(
            f"Fim do registro: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        log_content.append("=" * 100)
        log_content.append("")

        # Ler conteúdo anterior se arquivo existe
        previous_content = ""
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    previous_content = f.read()
            except Exception as e:
                logger.warning(f"Aviso ao ler arquivo de log anterior: {str(e)}")

        # Escrever novo conteúdo + anterior (prepend)
        full_content = "\n".join(log_content) + previous_content

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        logger.info(f"✓ Log de bancas salvo em '{log_path}'")
        return True

    except Exception as e:
        logger.error(f"Erro ao registrar bancas processadas: {str(e)}", exc_info=True)
        return False


# ============================================================================
# EXPORTAÇÃO PARA JSON
# ============================================================================


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
        json_path = OUT_DIR / filename

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(tests, f, indent=2, ensure_ascii=False)

        logger.info(
            f"✓ Exportação JSON concluída: {len(tests)} provas em '{json_path}'"
        )
        return True

    except Exception as e:
        logger.error(f"Erro ao exportar JSON: {str(e)}", exc_info=True)
        return False


# ============================================================================
# RELATÓRIOS DE ERRO E ESTATÍSTICAS
# ============================================================================


def save_error_report(stats, filename="error_report.json"):
    """
    Salva relatório detalhado de erros em JSON.

    Args:
        stats (dict): Dicionário com estatísticas
        filename (str): Nome do arquivo de saída

    Returns:
        bool: True se sucesso, False se erro
    """
    try:
        report_path = OUT_DIR / filename
        report_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "timestamp": datetime.now().isoformat(),
            "resumo": {
                "total_urls_coletadas": stats.get("total_urls_collected", 0),
                "total_provas_processadas": stats.get("successful_tests", 0),
                "total_provas_com_erro": stats.get("failed_tests", 0),
                "total_provas_sem_erro": stats.get("successful_tests", 0)
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

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Relatório de erros salvo em '{report_path}'")
        return True

    except Exception as e:
        logger.error(f"Erro ao salvar relatório: {str(e)}", exc_info=True)
        return False


def save_statistics_report(stats, elapsed_time, filename="statistics.json"):
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
        report_path = OUT_DIR / filename
        report_path.parent.mkdir(parents=True, exist_ok=True)

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

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Relatório de estatísticas salvo em '{report_path}'")
        return True

    except Exception as e:
        logger.error(
            f"Erro ao salvar relatório de estatísticas: {str(e)}", exc_info=True
        )
        return False
