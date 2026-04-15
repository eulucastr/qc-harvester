from src.exporters import export_data_to_csv
from src.scraper import scrape_tests

scraper_config: dict = {
    "bancas": [
        { "nome": "FGV", "código": 63 },
        { "nome": "CEBRASPE", "código": 2 },
    ],
    "anos": [2020, 2021, 2022, 2023],
}

def main():
    main_url: str = "https://www.qconcursos.com/questoes-de-concursos/provas"
    
    tests: list = scrape_tests(main_url, scraper_config)
    export_data_to_csv(tests)

if __name__ == "__main__":
    main()