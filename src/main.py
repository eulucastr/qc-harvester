import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from exporters import export_data_to_csv
from scraper import scrape_tests

with open('scraper_config.json', 'r') as file:
    scraper_config = json.load(file)

def main():
    main_url: str = "https://www.qconcursos.com/questoes-de-concursos/provas"
    
    tests: list = scrape_tests(main_url, scraper_config)
    export_data_to_csv(tests)

if __name__ == "__main__":
    main()