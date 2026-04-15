"""
PCI Concursos Harvester - Módulo de extração de provas

Pacote para coleta massiva de provas do site PCI Concursos.

Módulos:
- performance: Configurações e otimizações de performance
- scraper: Funções principais de scraping
- exporters: Funções de exportação de dados

Exemplo de uso:
    from src.scraper import get_roles
    from src.exporters import export_tests_to_csv

    tests = get_roles("https://www.pciconcursos.com.br/provas", ["fgv", "cebraspe"])
    export_tests_to_csv(tests)
"""

__version__ = "1.0.0"
__author__ = "PCI Concursos Harvester"
__all__ = [
    "get_roles",
    "get_exams",
    "get_test",
    "stats",
    "export_tests_to_csv",
    "save_error_report",
    "save_statistics_report",
    "create_resilient_scraper",
    "rate_limited_get",
]

# Importações dos módulos principais
try:
    from .exporters import (
        export_tests_to_csv,
        save_error_report,
        save_statistics_report,
    )
    from .performance import create_resilient_scraper, rate_limited_get
    from .scraper import get_exams, get_roles, get_test, stats
except ImportError as e:
    print(f"Erro ao importar módulos: {e}")
