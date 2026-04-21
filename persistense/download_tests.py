import asyncio
import os
import random
import re
import sqlite3

import httpx

# Configurações
DB_NAME = "mentoria-provas.db"
BASE_DIR = "D:/mentor.ia"
CONCURRENT_DOWNLOADS = (
    10  # Quantos downloads simultâneos (não exagere para não ser bloqueado)
)

# User-Agents rotativos para parecer navegador real
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0",
]

# Criar as pastas de destino
os.makedirs(f"{BASE_DIR}/provas", exist_ok=True)
os.makedirs(f"{BASE_DIR}/gabaritos", exist_ok=True)


def clean_filename(name):
    """Remove caracteres que o Windows proíbe em nomes de arquivos"""
    return re.sub(r"[\\/*?:\"<>|]", "_", str(name)).lower()


def get_random_user_agent():
    """Retorna um User-Agent aleatório para simular navegadores diferentes"""
    return random.choice(USER_AGENTS)


async def random_sleep():
    """Espera um tempo aleatório entre 3 e 7 segundos para não parecer robótico"""
    delay = random.uniform(1, 3)
    await asyncio.sleep(delay)


async def download_file(
    client, url, folder, exam_id, file_type, file_name, max_retries=3
):
    if not url or str(url).strip() == "" or str(url) == "nan":
        return False

    os.makedirs(folder, exist_ok=True)

    extension = ".pdf"
    clean_name = clean_filename(file_name)
    filename = f"{exam_id}_{file_type}_{clean_name}{extension}"
    final_path = os.path.join(folder, filename)

    # Backoff exponencial: começa em 1 minuto (60 segundos)
    base_delay = 60

    for attempt in range(max_retries):
        try:
            # User-Agent aleatório para cada requisição
            headers = {"User-Agent": get_random_user_agent()}

            response = await client.get(
                url, headers=headers, timeout=30.0, follow_redirects=True
            )

            if response.status_code == 200:
                with open(final_path, "wb") as f:
                    f.write(response.content)
                print(f"✅ {file_type} {exam_id} baixado com sucesso")
                return final_path
            elif response.status_code == 403:
                print(f"⛔ Erro 403 (não autorizado) ao baixar {file_type} {exam_id}")
                return "nao_autorizado"
            else:
                print(f"⚠️ Erro {response.status_code} ao baixar {file_type} {exam_id}")
                return False

        except Exception as e:
            current_attempt = attempt + 1
            print(
                f"❌ Erro na conexão {file_type} {exam_id}: {e} (tentativa {current_attempt}/{max_retries})"
            )

            # Se não for a última tentativa, aplicar backoff exponencial
            if current_attempt < max_retries:
                delay = base_delay * (2**attempt)  # 1 min, 2 min, 4 min...
                print(f"⏳ Aguardando {delay}s antes de tentar novamente...")
                await asyncio.sleep(delay)

    print(f"❌ Falha ao baixar {file_type} {exam_id} após {max_retries} tentativas")
    return False


async def process_row(client, row, semaphore, conn, db_lock):
    exam_id, board, year, institution, position, url_exam, url_answer = row

    try:
        async with semaphore:
            print(f"🚀 Iniciando ID {exam_id}...")

            base_name = f"{institution}_{position}_{year}"
            board_name = clean_filename(board) if board else "outras"

            exam_folder = os.path.join(BASE_DIR, "provas", board_name)
            answer_folder = os.path.join(BASE_DIR, "gabaritos", board_name)

            path_exam = await download_file(
                client, url_exam, exam_folder, exam_id, "prova", base_name
            )
            await random_sleep()
            path_answer = await download_file(
                client, url_answer, answer_folder, exam_id, "gabarito", base_name
            )

            if path_exam == "nao_autorizado" or path_answer == "nao_autorizado":
                print(
                    f"⚠️ ID {exam_id} não autorizado para download. Marcando como 'nao_autorizado' no banco."
                )
                async with db_lock:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE concursos SET status_download = 'nao_autorizado' WHERE id = ?",
                        (exam_id,),
                    )
                    conn.commit()
                return
            if path_exam and path_answer:
                try:
                    async with db_lock:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE concursos SET status_download = 'concluido', prova_path = ?, gabarito_path = ? WHERE id = ?",
                            (path_exam, path_answer, exam_id),
                        )
                        conn.commit()
                    print(f"✅ ID {exam_id} finalizado.")
                except sqlite3.OperationalError as db_err:
                    print(f"⚠️ Erro de gravação no banco no ID {exam_id}: {db_err}")

    except Exception as e:
        print(f"💥 Erro fatal inesperado no ID {exam_id}: {e}")


async def main():
    conn = sqlite3.connect(DB_NAME)

    # 💡 Aumentar o timeout nativo do SQLite também ajuda como camada extra de defesa
    # conn = sqlite3.connect(DB_NAME, timeout=10.0)

    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, banca, ano, instituicao, cargo, prova_url, gabarito_url FROM concursos WHERE status_download = 'pendente'"
    )
    rows = cursor.fetchall()

    if not rows:
        print("🏁 Nada para baixar!")
        return

    semaphore = asyncio.Semaphore(CONCURRENT_DOWNLOADS)

    db_lock = asyncio.Lock()

    limits = httpx.Limits(max_connections=CONCURRENT_DOWNLOADS + 5)
    async with httpx.AsyncClient(limits=limits) as client:
        # Passamos o db_lock para cada linha processada
        tasks = [process_row(client, row, semaphore, conn, db_lock) for row in rows]

        # Mantemos o return_exceptions=True para resiliência
        await asyncio.gather(*tasks, return_exceptions=True)

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
