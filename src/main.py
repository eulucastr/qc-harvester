import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from exporters import export_data_to_csv
from scraper import close_scraper, scrape_tests

with open("scraper_config.json", "r") as file:
    scraper_config = json.load(file)

def main():
    main_url: str = "https://www.qconcursos.com/questoes-de-concursos/provas"

    try:
        print("Iniciando scraping...")
        tests: list = scrape_tests(main_url, scraper_config)

        print(f"✓ Scraping concluído! {len(tests)} provas encontradas.")

        print("Exportando dados para CSV...")
        export_data_to_csv(tests)
        print("✓ Exportação concluída!")
    except Exception as e:
        print(f"✗ Erro durante a execução: {e}")
    finally:
        print("Fechando navegador...")
        close_scraper()


if __name__ == "__main__":
    main()
