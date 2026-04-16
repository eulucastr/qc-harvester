import csv
import shutil
from datetime import datetime
from pathlib import Path


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
    out_dir = Path("out")
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
        "órgão",
        "cargo",
        "função",
        "aplicação",
        "escolaridade",
        "prova",
        "gabarito",
        "alterações",
        "edital",
    ]

    # Remove duplicatas mantendo ordem
    # Uma duplicata é quando TODAS as colunas têm os mesmos valores
    seen = set()
    unique_tests = []
    for test in combined_tests:
        # Cria chave única com TODAS as colunas (na ordem de keys)
        key = tuple(test.get(k, "") for k in keys)
        if key not in seen:
            seen.add(key)
            unique_tests.append(test)

    # Escreve o arquivo
    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=keys)
        writer.writeheader()
        writer.writerows(unique_tests)

    print(f"✓ Dados exportados para {csv_path}")
    print(f"  Total de testes únicos: {len(unique_tests)}")
