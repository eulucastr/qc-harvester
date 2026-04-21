import json
import time

from .exporters import export_data_to_csv, log_success
from .scraper import close_scraper, scrape_tests

# Carrega configuração
with open("config/scraper_config.json", "r", encoding="utf-8") as file:
    scraper_config = json.load(file)

def main():
    """
    Função principal que orquestra todo o processo de scraping,
    exportação e logging.
    """
    start_time = time.time()
    main_url = "https://www.qconcursos.com/questoes-de-concursos/provas"

    try:
        print("\n" + "=" * 70)
        print("INICIANDO RASPAGEM DE PROVAS")
        print("=" * 70)

        # Faz o scraping
        print("\n1. Raspando dados...")
        tests = scrape_tests(main_url, scraper_config)

        if not tests:
            print("⚠ Nenhum teste foi extraído!")
            return

        print(f"\n2. Exportando {len(tests)} teste(s) para CSV...")
        export_data_to_csv(tests)

        # Calcula tempo total
        tempo_total_minutos = (time.time() - start_time) / 60

        # Extrai informações para log (usando nomes das bancas)
        bancas = [b.get("nome", "") for b in scraper_config.get("bancas", [])]
        anos = scraper_config.get("anos", [])

        # Registra sucesso
        print("\n3. Registrando sucesso em log...")
        log_success(bancas, anos, len(tests), tempo_total_minutos)

        print("\n" + "=" * 70)
        print("✓ PROCESSO CONCLUÍDO COM SUCESSO!")
        print("=" * 70)
        print(f"Total de provas extraídas: {len(tests)}")
        print(f"Tempo total: {tempo_total_minutos:.2f} minutos")
        print("=" * 70 + "\n")

    except Exception as e:
        print(f"\n✗ ERRO DURANTE A EXECUÇÃO: {e}")
        print("Verifique o arquivo de log de erros em /out/errors.log")

    finally:
        print("Limpando recursos...")
        close_scraper()


if __name__ == "__main__":
    main()
