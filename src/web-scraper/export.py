import csv
import shutil
from datetime import datetime
from pathlib import Path

out_dir = Path("output/web-scraper")

def export_data_to_csv(tests, filename="provas.csv"):
    """
    Exporta dados para CSV com backup automático e append de dados.

    Args:
        tests: Lista de dicionários com dados dos testes
        filename: Nome do arquivo CSV (padrão: provas.csv)
    """
    if not tests:
        print("Nenhum teste para exportar.")
        return

    # Diretórios
    backups_dir = out_dir / "backups"
    csv_path = out_dir / filename

    # Cria diretórios se não existirem
    out_dir.mkdir(exist_ok=True)
    backups_dir.mkdir(exist_ok=True)

    # Faz backup do arquivo atual se existir
    if csv_path.exists():
        timestamp = datetime.now().strftime("%d-%m-%Y-%H-%M")
        backup_name = f"provas-{timestamp}.csv"
        backup_path = backups_dir / backup_name
        shutil.copy2(csv_path, backup_path)
        print(f"✓ Backup criado: {backup_path}")

    # Lê dados existentes
    existing_tests = []
    if csv_path.exists():
        with open(csv_path, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            existing_tests = list(reader) if reader else []

    # Combina dados existentes com novos
    combined_tests = existing_tests + tests

    # Define as colunas
    keys = [
        "banca",
        "ano",
        "instituição",
        "estado",
        "cargo",
        "especialidade",
        "aplicação",
        "escolaridade",
    ]

    # Remove duplicatas mantendo ordem
    # Uma duplicata é quando todas as colunas têm os mesmos valores
    seen = set()
    unique_tests = []
    for test in combined_tests:
        key = tuple(test.get(k, "") for k in keys)
        if key not in seen:
            seen.add(key)
            unique_tests.append(test)
    
    keys.extend([ "prova", "gabarito", "alterações", "edital" ])
    # Escreve o arquivo
    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=keys)
        writer.writeheader()
        writer.writerows(unique_tests)

    print(f"✓ Dados exportados para {csv_path}")
    print(f"  Total de testes únicos: {len(unique_tests)}")


def log_error(page: int, bancas: list, anos: list, error_message: str):
    """
    Registra erros de raspagem em um arquivo de log.

    Args:
        page: Número da página que falhou
        bancas: Lista de nomes das bancas sendo raspadas
        anos: Lista de anos sendo raspados
        error_message: Mensagem de erro
    """
    out_dir.mkdir(exist_ok=True)

    error_log_path = out_dir / "errors.log"
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    bancas_str = ", ".join(bancas)
    anos_str = ", ".join(map(str, anos))

    error_entry = f"""
[{timestamp}] ERRO NA RASPAGEM DA PÁGINA {page}
Bancas: {bancas_str}
Anos: {anos_str}
{"=" * 60}
"""

    with open(error_log_path, "a", encoding="utf-8") as log_file:
        log_file.write(error_entry)

    print(f"⚠ Erro registrado em {error_log_path}")


def log_success(bancas: list, anos: list, total_provas: int, tempo_minutos: float):
    """
    Registra o sucesso de uma raspagem completa em um arquivo de log.

    Args:
        bancas: Lista de nomes das bancas raspadas
        anos: Lista de anos raspados
        total_provas: Quantidade total de provas extraídas nesta execução
        tempo_minutos: Tempo total em minutos
    """
    out_dir.mkdir(exist_ok=True)

    success_log_path = out_dir / "success.log"
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

    # Formata as listas para exibição
    bancas_str = ", ".join(bancas) if bancas else "Nenhuma"
    anos_str = ", ".join(map(str, anos)) if anos else "Nenhum"

    success_entry = f"""
[{timestamp}] RASPAGEM CONCLUÍDA COM SUCESSO
  Bancas: {bancas_str}
  Anos: {anos_str}
  Provas extraídas: {total_provas}
  Tempo total: {tempo_minutos:.2f} minutos
{"=" * 60}
"""

    with open(success_log_path, "a", encoding="utf-8") as log_file:
        log_file.write(success_entry)

    print(f"✓ Sucesso registrado em {success_log_path}")
