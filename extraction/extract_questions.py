import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
from typing import Optional

import fitz
from dotenv import load_dotenv
from google import genai
from PIL import Image

# ──────────────────────────────────────────────
# Configuração
# ──────────────────────────────────────────────

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=API_KEY)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "mentoria-provas.db")
PROMPT_PATH = os.path.join(BASE_DIR, "prompt.md")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
OUTPUT_DIR = "D:/mentor.ia/questões"
IMAGES_DIR = os.path.join(OUTPUT_DIR, "imagens")

MODEL_NAME = "gemini-3.1-flash-lite-preview"
MAX_CONCURRENT_IA = 5
BATCH_SIZE = 5

# Rate Limiter
MAX_REQUESTS_PER_MINUTE = 15
MAX_REQUESTS_PER_EXECUTION = 500
REQUESTS_MADE = 0
REQUEST_TIMES = []  # Janela móvel (últimos 60 segundos)


# ──────────────────────────────────────────────
# Rate Limiter
# ──────────────────────────────────────────────


def esperar_rate_limit():
    """Aguarda para respeitar 15 req/min (máx 500 por execução)."""
    global REQUESTS_MADE, REQUEST_TIMES

    # Verificar limite total
    if REQUESTS_MADE >= MAX_REQUESTS_PER_EXECUTION:
        print(
            f"❌ Limite de {MAX_REQUESTS_PER_EXECUTION} requisições atingido. Finalizando."
        )
        sys.exit(1)

    # Remover requisições fora da janela de 60 segundos
    agora = time.time()
    REQUEST_TIMES = [t for t in REQUEST_TIMES if agora - t < 60]

    # Se já há 15 requisições no último minuto, aguardar
    if len(REQUEST_TIMES) >= MAX_REQUESTS_PER_MINUTE:
        tempo_espera = 60 - (agora - REQUEST_TIMES[0])
        print(f"⏳ Rate limit: aguardando {tempo_espera:.1f}s...")
        time.sleep(tempo_espera)
        REQUEST_TIMES = []

    REQUEST_TIMES.append(time.time())
    REQUESTS_MADE += 1

    print(
        f"📊 Requisições: {REQUESTS_MADE}/{MAX_REQUESTS_PER_EXECUTION} | {len(REQUEST_TIMES)}/15 neste minuto"
    )


# ──────────────────────────────────────────────
# Conversão de PDFs
# ──────────────────────────────────────────────


def convert_pdf(file_path: str, doc_type: str, concurso_id: int) -> str:
    """Converte cada página de um PDF em imagem PNG usando PyMuPDF."""
    dest_dir = os.path.join(TEMP_DIR, doc_type, str(concurso_id))
    os.makedirs(dest_dir, exist_ok=True)

    print(f"  📄 Convertendo {doc_type} (ID {concurso_id})")

    try:
        doc = fitz.open(file_path)
        page_count = len(doc)

        if page_count == 0:
            raise Exception("PDF vazio ou inválido")

        for page_num in range(page_count):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_path = os.path.join(dest_dir, f"{page_num}.png")
            pix.save(img_path)

        doc.close()
        print(f"  ✅ {page_count} página(s) → {doc_type}")
        return dest_dir

    except Exception as e:
        raise Exception(f"Erro ao converter PDF: {e}")


# ──────────────────────────────────────────────
# Processamento com IA
# ──────────────────────────────────────────────


def process_test(pasta_prova: str, pasta_gabarito: str) -> Optional[str]:
    """Envia imagens para Gemini e retorna resposta JSON."""

    # Aplicar rate limiter ANTES de fazer a requisição
    esperar_rate_limit()

    try:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            prompt_text = f.read()
    except Exception as e:
        print(f"  ❌ Erro ao carregar prompt.md: {e}")
        return None

    try:
        imgs_prova = sorted(
            [f for f in os.listdir(pasta_prova) if f.endswith(".png")],
            key=lambda x: int(os.path.splitext(x)[0]),
        )
        imgs_gabarito = sorted(
            [f for f in os.listdir(pasta_gabarito) if f.endswith(".png")],
            key=lambda x: int(os.path.splitext(x)[0]),
        )
    except Exception as e:
        print(f"  ❌ Erro ao carregar imagens: {e}")
        return None

    if not imgs_prova or not imgs_gabarito:
        print("  ❌ Imagens não encontradas")
        return None

    # Montar conteúdo
    contents: list = [prompt_text]
    contents.append("\n--- INÍCIO DAS IMAGENS DA PROVA ---\n")

    for img_name in imgs_prova:
        img_path = os.path.join(pasta_prova, img_name)
        img = Image.open(img_path)
        contents.append(img)

    contents.append("\n--- INÍCIO DAS IMAGENS DO GABARITO ---\n")

    for img_name in imgs_gabarito:
        img_path = os.path.join(pasta_gabarito, img_name)
        img = Image.open(img_path)
        contents.append(img)

    print(f"  📦 Enviando para IA ({len(imgs_prova)} prov + {len(imgs_gabarito)} gab)")

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        print("  ✅ Resposta recebida")
        return response.text
    except Exception as e:
        print(f"  ❌ Erro na IA: {e}")
        return None


# ──────────────────────────────────────────────
# Extração de Imagens de Questões
# ──────────────────────────────────────────────


def realizar_recorte_automatico(
    caminho_pagina_original: str, coordenadas_ia, nome_saida: str
):
    """
    Extrai uma questão de uma página usando coordenadas normalizadas.

    Args:
        caminho_pagina_original: Caminho da página PNG
        coordenadas_ia: [ymin, xmin, ymax, xmax] normalizados (0-1000)
        nome_saida: Caminho completo para salvar a imagem recortada
    """
    try:
        img = Image.open(caminho_pagina_original)
        largura, altura = img.size

        # Desnormalizar (Converter 0-1000 para pixels reais)
        ymin, xmin, ymax, xmax = coordenadas_ia

        esquerda = (xmin * largura) / 1000
        topo = (ymin * altura) / 1000
        direita = (xmax * largura) / 1000
        fundo = (ymax * altura) / 1000

        # Fazer o crop
        area_recorte = (esquerda, topo, direita, fundo)
        imagem_recortada = img.crop(area_recorte)

        # Garantir diretório
        os.makedirs(os.path.dirname(nome_saida), exist_ok=True)

        # Salvar
        imagem_recortada.save(nome_saida)
        print(f"    📸 Imagem salva: {os.path.basename(nome_saida)}")

    except Exception as e:
        print(f"    ❌ Erro ao recortar: {e}")


def extrair_imagens_questoes(
    resultado_json: str, pasta_prova: str, pasta_gabarito: str, concurso_id: int
):
    """
    Extrai imagens das questões usando as coordenadas retornadas pela IA.

    Args:
        resultado_json: JSON retornado pela IA
        pasta_prova: Pasta com imagens da prova
        pasta_gabarito: Pasta com imagens do gabarito
        concurso_id: ID do concurso (para criar pasta de imagens)
    """
    try:
        dados = json.loads(resultado_json)
    except Exception as e:
        print(f"  ❌ Erro ao parsear JSON para extração de imagens: {e}")
        return

    questoes = dados.get("questoes", [])
    if not questoes:
        print("  ⚠️  Nenhuma questão encontrada no JSON")
        return

    print(f"  🖼️  Extraindo imagens de {len(questoes)} questões...")

    for questao in questoes:
        numero = questao.get("numero")
        imagens = questao.get("imagens", [])

        if not imagens:
            continue

        for idx, img_info in enumerate(imagens):
            index_pagina = img_info.get("index_da_pagina")
            coordenadas = img_info.get("coordenadas")

            if index_pagina is None or not coordenadas:
                continue

            # Caminho da página original
            pagina_original = os.path.join(pasta_prova, f"{index_pagina}.png")

            if not os.path.exists(pagina_original):
                print(f"    ⚠️  Página {index_pagina} não encontrada")
                continue

            # Caminho de saída
            pasta_questoes_imagens = os.path.join(IMAGES_DIR, f"{concurso_id}")
            if idx > 0:
                nome_arquivo = f"{numero}_{idx}.png"
            else:
                nome_arquivo = f"{numero}.png"

            caminho_saida = os.path.join(pasta_questoes_imagens, nome_arquivo)

            # Extrair imagem
            realizar_recorte_automatico(pagina_original, coordenadas, caminho_saida)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def _sanitize(value: Optional[str]) -> str:
    """Remove caracteres inválidos para nomes de arquivo."""
    if not value:
        return "desconhecido"
    return value.strip().replace("/", "-").replace("\\", "-").replace(":", "-")


def _cleanup_temp(concurso_id: int):
    """Remove pastas temporárias."""
    for doc_type in ("prova", "gabarito"):
        path = os.path.join(TEMP_DIR, doc_type, str(concurso_id))
        if os.path.exists(path):
            shutil.rmtree(path)


# ──────────────────────────────────────────────
# Fase 1: Converter PDFs (paralelo, sem limite)
# ──────────────────────────────────────────────


async def converter_batch(batch, loop):
    """Converte prova + gabarito de um lote em paralelo (sem limite)."""
    print("[FASE 1] Convertendo PDFs em paralelo...")

    tasks = []

    for row in batch:
        concurso_id = row["id"]
        prova_path = row["prova_path"]
        gabarito_path = row["gabarito_path"]

        # Criar tarefas para converter prova e gabarito em paralelo
        task_prova = loop.run_in_executor(
            None, convert_pdf, prova_path, "prova", concurso_id
        )
        task_gabarito = loop.run_in_executor(
            None, convert_pdf, gabarito_path, "gabarito", concurso_id
        )

        tasks.append((concurso_id, task_prova, task_gabarito))

    # Aguardar todas as conversões
    resultados = {}
    for concurso_id, task_prova, task_gabarito in tasks:
        try:
            pasta_prova = await task_prova
            pasta_gabarito = await task_gabarito
            resultados[concurso_id] = {
                "sucesso": True,
                "pasta_prova": pasta_prova,
                "pasta_gabarito": pasta_gabarito,
            }
        except Exception as e:
            print(f"  ❌ Erro na conversão ID {concurso_id}: {e}")
            resultados[concurso_id] = {"sucesso": False}

    print(
        f"✅ Fase 1 concluída: {len([r for r in resultados.values() if r['sucesso']])} conversões OK\n"
    )
    return resultados


# ──────────────────────────────────────────────
# Fase 2: Processar com IA (paralelo, máx 10)
# ──────────────────────────────────────────────


async def processar_concurso_ia(
    row,
    pasta_prova,
    pasta_gabarito,
    semaphore,
    conn,
    output_dir,
    loop,
):
    """Processa um concurso com IA (dentro do semáforo de máx 10)."""
    concurso_id = row["id"]
    banca = _sanitize(row["banca"])
    instituicao = _sanitize(row["instituicao"])
    cargo = _sanitize(row["cargo"])
    especialidade = _sanitize(row["especialidade"])
    ano = row["ano"] if row["ano"] else "sem_ano"

    async with semaphore:
        print(f"🤖 IA processando ID {concurso_id}")

        # Executar process_test em thread
        resultado_json = await loop.run_in_executor(
            None, process_test, pasta_prova, pasta_gabarito
        )

        if resultado_json is None:
            print(f"  ⚠️  Extração falhou para ID {concurso_id}")
            return (concurso_id, False)

        # Salvar JSON
        nome_arquivo = (
            f"{concurso_id}-{banca}-{instituicao}-{cargo}-{especialidade}-{ano}.json"
        )
        caminho_json = os.path.join(output_dir, nome_arquivo)

        with open(caminho_json, "w", encoding="utf-8") as f:
            try:
                dados = json.loads(resultado_json)
                json.dump(dados, f, ensure_ascii=False, indent=4)
            except json.JSONDecodeError:
                f.write(resultado_json)

        print(f"  💾 JSON salvo: {os.path.basename(caminho_json)}")

        # Extrair imagens das questões
        print("  🖼️  Extraindo imagens...")
        extrair_imagens_questoes(
            resultado_json, pasta_prova, pasta_gabarito, concurso_id
        )

        # Atualizar banco
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE concursos SET questoes_path = ?, status_extracao = 'concluido' WHERE id = ?",
            (caminho_json, concurso_id),
        )
        conn.commit()
        print(f"  ✅ ID {concurso_id} finalizado\n")

        return (concurso_id, True)


async def processar_batch_ia(batch, conversoes, semaphore, conn, output_dir, loop):
    """Processa um lote com IA (máx 10 simultâneas)."""
    print("[FASE 2] Processando com IA (máx 10 simultâneas)...")

    tasks = []

    for row in batch:
        concurso_id = row["id"]

        # Se conversão falhou, pular
        if not conversoes.get(concurso_id, {}).get("sucesso", False):
            print(f"⏭️  ID {concurso_id} pulado (conversão falhou)")
            continue

        pasta_prova = conversoes[concurso_id]["pasta_prova"]
        pasta_gabarito = conversoes[concurso_id]["pasta_gabarito"]

        # Criar tarefa
        task = processar_concurso_ia(
            row, pasta_prova, pasta_gabarito, semaphore, conn, output_dir, loop
        )
        tasks.append(task)

    # Executar todas as tarefas IA em paralelo
    if tasks:
        resultados = await asyncio.gather(*tasks)
    else:
        resultados = []

    print("✅ Fase 2 concluída\n")
    return resultados


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────


async def main():
    global REQUESTS_MADE

    print("=" * 70)
    print("🚀 EXTRATOR DE QUESTÕES — Mentor.IA")
    print("=" * 70)
    print(
        f"⚙️  Rate Limit: {MAX_REQUESTS_PER_MINUTE}/min, {MAX_REQUESTS_PER_EXECUTION} max/execução\n"
    )

    # Conectar ao banco
    if not os.path.exists(DB_PATH):
        print(f"❌ Banco de dados não encontrado: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Buscar TODOS os concursos pendentes
    cursor.execute(
        "SELECT * FROM concursos WHERE status_extracao = 'pendente' AND prova_path IS NOT NULL AND gabarito_path IS NOT NULL"
    )
    pendentes = cursor.fetchall()

    if not pendentes:
        print("ℹ️  Nenhum concurso pendente para extração.")
        conn.close()
        return

    print(f"📊 Total de concursos pendentes: {len(pendentes)}")
    print(f"⚙️  Processando em lotes de {BATCH_SIZE}\n")

    # Garantir que os diretórios de saída existem
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

    # Criar semáforo para limitar IA a 10
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_IA)
    loop = asyncio.get_event_loop()

    # Dividir em lotes e processar
    total_concluidos = 0
    total_falhados = 0

    for lote_num in range(0, len(pendentes), BATCH_SIZE):
        # Verificar se atingiu limite de requisições
        if REQUESTS_MADE >= MAX_REQUESTS_PER_EXECUTION:
            print(f"\n❌ Limite de {MAX_REQUESTS_PER_EXECUTION} requisições atingido.")
            print(f"✅ Concluídos até agora: {total_concluidos}")
            break

        batch = pendentes[lote_num : lote_num + BATCH_SIZE]
        num_lote = (lote_num // BATCH_SIZE) + 1
        total_lotes = (len(pendentes) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n{'=' * 70}")
        print(f"📦 LOTE {num_lote}/{total_lotes} ({len(batch)} concursos)")
        print(f"{'=' * 70}\n")

        # Fase 1: Converter
        conversoes = await converter_batch(batch, loop)

        # Fase 2: Processar com IA
        resultados_ia = await processar_batch_ia(
            batch, conversoes, semaphore, conn, OUTPUT_DIR, loop
        )

        # Contar resultados
        concluidos_lote = sum(1 for _, sucesso in resultados_ia if sucesso)
        falhados_lote = len(resultados_ia) - concluidos_lote

        total_concluidos += concluidos_lote
        total_falhados += falhados_lote

        # Fase 3: Limpar
        print("[FASE 3] Limpando arquivos temporários...")
        for row in batch:
            _cleanup_temp(row["id"])

        print(
            f"✅ Lote {num_lote} finalizado: {concluidos_lote} OK, {falhados_lote} falhados\n"
        )

    conn.close()

    # Resumo final
    print("\n" + "=" * 70)
    print("🏁 Extração COMPLETA finalizada.")
    print(f"✅ Total concluídos: {total_concluidos}/{len(pendentes)}")
    print(f"❌ Total falhados: {total_falhados}/{len(pendentes)}")
    print(f"📊 Requisições realizadas: {REQUESTS_MADE}/{MAX_REQUESTS_PER_EXECUTION}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
