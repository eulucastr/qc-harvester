# QC Harvester

Simples scraper para coletar provas e gabaritos do site QConcursos.

## O que faz
- Navega pelo site de provas do QConcursos e extrai metadados e links das provas.
- Contorna proteções que exigem execução de JavaScript usando Selenium.
- Gera um CSV (`out/provas.csv`) com os resultados, cria backups e registra logs de sucesso/erro.

## Pré-requisitos
- Python 3.8 ou superior
- Chrome/Chromium instalado
- Chromedriver compatível com a versão do Chrome
- Pacotes Python:
  - selenium
  - beautifulsoup4
  - requests

Você pode instalar as dependências com pip:
```
pip install selenium beautifulsoup4 requests
```

## Estrutura de configuração
Arquivo: `scraper/scraper_config.json`

Formato esperado (exemplo):
```json
{
  "bancas": [
    { "nome": "FCC", "codigo": 1 },
    { "nome": "IBFC", "codigo": 189 },
    { "nome": "VUNESP", "codigo": 152 }
  ],
  "anos": [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016]
}
```

- Para montar as queries, o scraper usa o campo `codigo` das bancas.
- Para logs e exibição, usa o campo `nome`.

## Como executar
Rode a partir do diretório raiz do projeto (o diretório que contém a pasta `scraper`):

```
python -m scraper.main
```

Isso garante que imports relativos funcionem corretamente e que o scraper rode no contexto do pacote.

## Saída / Arquivos gerados
Depois da execução, verifique a pasta `out/` (é criada automaticamente):
- `out/provas.csv` — CSV principal com todas as provas (append + deduplicação).
- `out/backups/` — backups do arquivo `provas.csv` antes de cada exportação.
- `out/errors.log` — logs de páginas que falharam após todas as tentativas.
- `out/success.log` — histórico das execuções bem-sucedidas (bancas, anos, quantidade e tempo).

## Comportamento importante
- O scraper usa Selenium (navegador real) para contornar proteções baseadas em JavaScript/Cloudflare.
- Implementa:
  - retries por página,
  - fallback (parar carregamento e parsear conteúdo parcial),
  - rotação/reinício do navegador periodicamente para evitar vazamento de memória,
  - delays adaptativos para reduzir possibilidade de bloqueio por rate-limiting.
- Se uma página falhar após todas as tentativas, o erro é registrado em `out/errors.log` e a execução continua.

## Dicas de troubleshooting
- Se ocorrerem muitos `TimeoutException` em páginas profundas, tente:
  - Aumentar o timeout no código (`set_page_load_timeout`) ou aumentar o delay entre páginas.
  - Verificar versão do Chromedriver compatível com seu Chrome.
  - Rodar em modo não-headless (descomentar flag headless) para inspecionar o que está ocorrendo visualmente.
- Se `scraper_config.json` não for ignorado pelo git, verifique se já não foi commitado antes. Para parar de trackear localmente use `git rm --cached scraper_config.json` e commite a remoção.

## Licença
Veja o arquivo `LICENSE` no repositório.

---
Simples e direto — ajuste `scraper_config.json` conforme suas necessidades e execute conforme instruções acima.