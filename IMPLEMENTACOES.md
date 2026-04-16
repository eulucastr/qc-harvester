# Implementações de Robustez - PCI Harvester

## Resumo das Mudanças

Este documento descreve as implementações de robustez adicionadas ao scraper para contornar erros de timeout do Cloudflare/ChromeDriver e garantir a execução contínua mesmo em caso de falhas.

---

## 1. Estratégia de Retry com Fallback

### O Problema
O ChromeDriver ocasionalmente sofria timeout ao tentar carregar páginas, especialmente em páginas profundas (ex: page=205). O erro resultava em parada total da execução.

### A Solução
Implementado sistema de retry com 3 tentativas e fallback para carregamento parcial:

```python
for attempt in range(1, max_retries + 1):
    try:
        driver.get(page_url)
        WebDriverWait(driver, 15).until(...)
        break  # Sucesso
    except Exception:
        # Tenta parar o carregamento e parsear o que chegou
        driver.execute_script("window.stop();")
        # Se conseguir extrair elementos, aceita carregamento parcial
```

**Benefícios:**
- ✓ Tenta até 3 vezes antes de desistir
- ✓ Usa backoff exponencial (aguarda 3s, 6s, 9s entre tentativas)
- ✓ Fallback: se timeout, tenta `window.stop()` e parseia HTML parcial
- ✓ Não interrompe a execução - registra erro em log e continua

---

## 2. PageLoadStrategy = "eager"

### Configuração
```python
chrome_options.set_capability("pageLoadStrategy", "eager")
```

### O que faz
- Retorna após `DOMContentLoaded` (não aguarda todos os recursos)
- Reduz chance de timeout em páginas com recursos pesados
- Combinado com timeout de 90 segundos para maior flexibilidade

**Impacto:** Reduz tempo de carregamento e chance de timeout

---

## 3. Sistema de Logging de Erros

### Arquivo: `/out/errors.log`

Quando uma página falha em todas as 3 tentativas, o erro é registrado:

```
[15-02-2025 14:30:45] Página 205 - Erro: TimeoutException: Timeout waiting for element
  URL: https://www.qconcursos.com/questoes-de-concursos/provas?by_examining_board[]=1&...&page=205
────────────────────────────────────────────────────────────────────────────────
```

### Função
```python
log_error(page_number: int, url: str, error_message: str)
```

**Localização:** `src/exporters.py`

---

## 4. Sistema de Logging de Sucesso

### Arquivo: `/out/success.log`

Ao final de cada execução bem-sucedida:

```
[15-02-2025 14:45:30] RASPAGEM CONCLUÍDA COM SUCESSO
  Bancas: [1, 5, 41, 235]
  Anos: [2020, 2021, 2022, 2023]
  Provas extraídas: 2850
  Tempo total: 45.30 minutos
============================================================
```

### Função
```python
log_success(bancas: list, anos: list, total_provas: int, tempo_minutos: float)
```

**Localização:** `src/exporters.py`

---

## 5. Comportamento em Caso de Erro

### Antes
- Erro em página → Parada total da execução
- Nenhuma informação sobre qual página falhou
- Dados parciais perdidos

### Depois
- Erro em página → Registrado em log, execução continua
- Todas as páginas são processadas
- Ao final: mensagem de sucesso + quantidade de erros em log

**Exemplo de fluxo:**
```
Página 1-204: ✓ Sucesso
Página 205: ⚠ Falha (erro registrado em log) → continua
Página 206-...: ✓ Sucesso
Fim: ✓ Raspagem concluída com N erros em log
```

---

## 6. Novo Formato de Configuração

### Arquivo: `scraper_config.json`

**Novo formato (atual):**
```json
{
  "by_examining_board": [1, 5, 41, 235, 189, 152],
  "application_year": [2020, 2021, 2022, 2023, 2024, 2025, 2026]
}
```

**Antigas chaves:**
- `"by_examining_board"` - Códigos das bancas examinadoras
- `"application_year"` - Anos dos concursos

**Exemplo completo:**
```json
{
  "by_examining_board": [
    1,    // FCC
    5,    // CESGRANRIO
    41,   // CONSULPLAN
    235,  // QUADRIX
    189,  // IBFC
    152   // VUNESP
  ],
  "application_year": [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
}
```

---

## 7. Estrutura de Diretórios Gerados

Após executar o scraper, a seguinte estrutura é criada:

```
pci-harvester/
├── out/
│   ├── provas.csv              # CSV principal com todos os dados
│   ├── errors.log              # Erros de páginas que falharam
│   ├── success.log             # Histórico de execuções bem-sucedidas
│   └── backups/
│       ├── provas-15-02-2025-14-30.csv
│       ├── provas-15-02-2025-14-45.csv
│       └── ... (backup de cada execução)
```

---

## 8. Como Usar

### Passo 1: Configurar `scraper_config.json`
```json
{
  "by_examining_board": [1, 5, 41],
  "application_year": [2020, 2021, 2022]
}
```

### Passo 2: Executar
```bash
cd C:\Projetos\Mentor.ia\pci-harvester
python src/main.py
```

### Passo 3: Verificar Resultados
- **Dados extraídos:** `out/provas.csv`
- **Sucesso:** `out/success.log`
- **Erros (se houver):** `out/errors.log`
- **Backups:** `out/backups/`

---

## 9. Tratamento de Duplicatas

O sistema evita duplicatas comparando **TODAS as 11 colunas**:
- banca, ano, órgão, cargo, função
- aplicação, escolaridade, prova, gabarito, alterações, edital

**Apenas se TODOS os campos forem iguais**, é considerada duplicata e não é adicionada novamente.

---

## 10. Timeouts Configurados

| Componente | Timeout | Finalidade |
|-----------|---------|-----------|
| `pageLoadStrategy` | "eager" | Não aguarda recursos pesados |
| `set_page_load_timeout()` | 90s | Tempo máximo para carregar página |
| `WebDriverWait` | 15s | Tempo máximo para encontrar elementos |
| Retry | 3 tentativas | Tenta 3 vezes antes de desistir |
| Backoff | 3s, 6s, 9s | Espera entre tentativas |

---

## 11. Exemplo de Execução Completa

```
======================================================================
INICIANDO RASPAGEM DE PROVAS
======================================================================

1. Raspando dados...

============================================================
Total de páginas a raspar: 250
Bancas: [1, 5, 41]
Anos: [2020, 2021, 2022]
============================================================

Página 1/250...
  → Tentativa 1/3 para página 1...
✓ 20 prova(s) extraída(s) | Total: 20

Página 2/250...
  → Tentativa 1/3 para página 2...
✓ 20 prova(s) extraída(s) | Total: 40

... (páginas 3-204 OK)

Página 205/250...
  → Tentativa 1/3 para página 205...
  ⚠ Falha na tentativa 1: TimeoutException
  → Aguardando 3s antes da próxima tentativa...
  → Tentativa 2/3 para página 205...
  ⚠ Falha na tentativa 2: TimeoutException
  → Aguardando 6s antes da próxima tentativa...
  → Tentativa 3/3 para página 205...
  ⚠ Falha na tentativa 3: TimeoutException
  ✗ Página 205 falhou após 3 tentativas. Erro registrado em log.

Página 206/250...
  → Tentativa 1/3 para página 206...
✓ 18 prova(s) extraída(s) | Total: 5018

... (restante das páginas OK)

============================================================
✓ RASPAGEM CONCLUÍDA!
Total de provas extraídas: 5000
============================================================

2. Exportando 5000 teste(s) para CSV...
✓ Backup criado: out/backups/provas-15-02-2025-14-50.csv
✓ Dados exportados para out/provas.csv
  Total de testes únicos: 5000

3. Registrando sucesso em log...
✓ Sucesso registrado em out/success.log

======================================================================
✓ PROCESSO CONCLUÍDO COM SUCESSO!
======================================================================
Total de provas extraídas: 5000
Tempo total: 52.45 minutos
======================================================================
```

---

## 12. Monitoramento e Diagnóstico

### Para verificar erros
```bash
cat out/errors.log
```

### Para verificar sucesso
```bash
cat out/success.log
```

### Para ver dados extraídos
```bash
# Ver primeiras linhas do CSV
head -n 20 out/provas.csv

# Contar total de linhas (provas + header)
wc -l out/provas.csv
```

---

## 13. Notas Importantes

1. **Todas as páginas são processadas** - Mesmo com erros em algumas páginas, o scraper continua
2. **Nenhum dado é perdido** - Erros em determinadas páginas não afetam as outras
3. **Logs acumulativos** - `errors.log` e `success.log` acumulam histórico de todas as execuções
4. **Backups automáticos** - Cada execução cria backup do CSV anterior antes de modificar
5. **Deduplicação** - Dados duplicados são automaticamente removidos ao adicionar ao CSV
6. **Compatibilidade** - Funciona com Selenium 4.15+, Python 3.8+, Chrome/Chromium

---

## 14. Solução de Problemas

### Problema: Muitos erros em `errors.log`
**Causa:** Cloudflare bloqueando muitas requisições seguidas  
**Solução:** Aumentar delay entre páginas em `scraper.py` linha ~243: `time.sleep(2)` → `time.sleep(5)`

### Problema: Chrome travando
**Causa:** Falta de recursos (memória/CPU)  
**Solução:** Fechar outros aplicativos ou dividir em múltiplas execuções com filtros diferentes

### Problema: Timeout continua mesmo com retries
**Causa:** Cloudflare muito restritivo  
**Solução:** Usar VPN ou executar em horários diferentes

---

## Referência Rápida

| Arquivo | Função |
|---------|--------|
| `main.py` | Orquestra execução, chama scraper e exportador |
| `scraper.py` | Faz scraping com retry logic e pageLoadStrategy eager |
| `exporters.py` | Exporta CSV, faz backup, registra logs |
| `errors.log` | Páginas que falharam em todas as tentativas |
| `success.log` | Histórico de execuções bem-sucedidas |
| `provas.csv` | Dados principais com deduplicação |

---

**Versão:** 2.0 (Com Robustez)  
**Data:** Fevereiro 2025  
**Status:** Produção
