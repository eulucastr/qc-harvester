import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from exporters import export_data_to_csv
from scraper import scrape_tests

scraper_config: dict = {
    "bancas": [
        { "nome": "FGV", "código": 63 },
    ],
    "anos": [2020],
}

def main():
    main_url: str = "https://www.qconcursos.com/questoes-de-concursos/provas"
    
    tests: list = scrape_tests(main_url, scraper_config)
    export_data_to_csv(tests)

if __name__ == "__main__":
    main()