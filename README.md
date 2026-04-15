# 🚀 Guia Rápido - Novas Funcionalidades

## 🎯 Como Usar

### Primeira Execução

```bash
python src/main.py
```

**Resultado:**
```
out/
├── provas.csv         # Criado com cabeçalho
├── bancas.log.txt     # Criado com registro
├── error_report.json  # Criado com erros (se houver)
└── statistics.json    # Criado com stats
```

### Executar Novamente

```bash
python src/main.py
```

**Resultado:**
- ✅ Novos registros adicionados ao `provas.csv`
- ✅ Duplicatas removidas automaticamente
- ✅ Novo registro adicionado ao início do `bancas.log.txt`
- ✅ Histórico anterior preservado

---

## 📊 Exemplo de Saída

### Conteúdo de `out/bancas.log.txt`

```
====================================================================================================
REGISTRO DE PROCESSAMENTO DE BANCAS - PCI HARVESTER
====================================================================================================
Data/Hora: 2024-01-15 14:30:45

INFORMAÇÕES GERAIS
----------------------------------------------------------------------------------------------------
Bancas processadas: 1
Total de provas coletadas: 250
Provas com sucesso: 248
Provas com erro: 2
Taxa de sucesso: 99.20%
Tempo total de execução: 145.67s (2.43 minutos)
Velocidade média: 1.70 provas/segundo

BANCAS PROCESSADAS
----------------------------------------------------------------------------------------------------
1. FGV

ESTATÍSTICAS DETALHADAS
----------------------------------------------------------------------------------------------------
Páginas processadas: 15
URLs coletadas: 250
Provas exportadas com sucesso: 248
Provas com falha: 2
Roles/Bancas processadas: 1

====================================================================================================
Fim do registro: 2024-01-15 14:30:45
====================================================================================================

[... registros de execuções anteriores ...]
```

### Conteúdo de `out/provas.csv`

```csv
cargo,ano,entidade,banca,prova,gabarito
Analista,2023,BANCO DO BRASIL,FGV,http://exemplo.com/prova1.pdf,http://exemplo.com/gab1.pdf
Técnico,2023,CAIXA,FGV,http://exemplo.com/prova2.pdf,http://exemplo.com/gab2.pdf
...
```

---

## 🔍 Verificar Resultados

### Ver dados adicionados

```bash
# Windows
type out\provas.csv | more

# Linux/Mac
cat out/provas.csv | head -20
```

### Ver histórico de bancas

```bash
# Windows
type out\bancas.log.txt | more

# Linux/Mac
cat out/bancas.log.txt | head -50
```

### Ver estatísticas

```bash
# Windows
type out\statistics.json | more

# Linux/Mac
cat out/statistics.json | less
```

---

## ⚙️ Configuração

### Adicionar Mais Bancas

Editar `src/main.py`:

```python
BANCAS_LISTA = [
    "fgv",           # Existente
    "cebraspe",      # Adicionar nova
    "cesgranrio",    # Adicionar nova
]
```

Executar novamente:
```bash
python src/main.py
```

Resultado:
- ✅ Novas bancas processadas
- ✅ Dados anteriores preservados
- ✅ Log atualizado com todas as bancas

---

## 📈 Exemplos de Uso

### Uso 1: Processamento Periódico

```bash
python src/main.py

# Arquivo CSV acumula dados
# Histórico em bancas.log.txt cresce
```

### Uso 2: Adicionar Novas Bancas

```bash
# Editar lista de bancas
# Executar novamente
python src/main.py

# Dados anteriores preservados
# Novos dados adicionados
```

### Uso 3: Backup
```bash
# Copiar arquivo CSV antes de grande mudança
cp out/provas.csv out/provas_backup.csv

# Fazer mudanças
# Se algo der errado, restaurar backup
```

---

## ✅ Verificação de Qualidade

### Verificar se tudo funcionou

1. **Arquivo CSV criado?**
   ```bash
   ls -la out/provas.csv
   ```

2. **Log de bancas criado?**
   ```bash
   ls -la out/bancas.log.txt
   ```

3. **Dados sendo adicionados?**
   - Comparar tamanho do CSV antes e depois
   - Verificar número de linhas

4. **Sem duplicatas?**
   - Verificar se URLs repetem em CSV
   - Consultar log de erros

---

## 🐛 Solução de Problemas

### Problema: Arquivo CSV vazio

**Causa**: Nenhuma prova foi coletada  
**Solução**: Verificar URL e conexão com site

### Problema: Duplicatas no CSV

**Causa**: URL não foi detectada corretamente  
**Solução**: Limpar arquivo e rodar novamente

### Problema: Erro ao escribir arquivo

**Causa**: Permissão negada na pasta /out  
**Solução**: Verificar permissões: `chmod 755 out/`

### Problema: Log muito grande

**Causa**: Muitas execuções acumuladas  
**Solução**: Rotacionar: `mv out/bancas.log.txt out/bancas.log.backup.txt`

---

## 📞 Contato

Se tiver dúvidas ou problemas:

1. Verificar logs em `logs/pci_harvester.log`
2. Verificar erros em `logs/pci_harvester_errors.log`
3. Revisar `out/error_report.json` para URLs com erro

---

## 📚 Documentação Completa

Para documentação mais detalhada:
- `IMPLEMENTACOES.md` - Detalhes técnicos
- `REVISAO_CODIGO.md` - Revisão de código
- `MODULAR_STRUCTURE.md` - Estrutura do projeto

---

**Última Atualização**: 2024  
**Versão**: 1.0  
**Status**: ✅ Pronto para Produção