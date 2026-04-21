import asyncio
import os
import random
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import httpx

# Configurações
DB_NAME = "mentoria.db"
BASE_DIR = "D:/mentor.ia"
CONCURRENT_DOWNLOADS = 10
LOG_DIR = "output"
LOG_FILE = "tests-downloads.log"

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
os.makedirs(LOG_DIR, exist_ok=True)


@dataclass
class DownloadStats:
    """Armazena estatísticas de download"""

    total_exams: int = 0
    successful_exams: int = 0
    failed_exams: int = 0
    unauthorized_exams: int = 0
    exam_files_downloaded: int = 0
    answer_files_downloaded: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None

    @property
    def total_files_downloaded(self) -> int:
        return self.exam_files_downloaded + self.answer_files_downloaded

    @property
    def duration(self) -> timedelta:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return timedelta(0)

    @property
    def average_time_per_exam(self) -> timedelta:
        if self.successful_exams > 0:
            return self.duration / self.successful_exams
        return timedelta(0)


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


async def process_row(client, row, semaphore, conn, db_lock, stats, stats_lock):
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
                async with stats_lock:
                    stats.unauthorized_exams += 1
                async with db_lock:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE concursos SET status_download = 'nao_autorizado' WHERE id = ?",
                        (exam_id,),
                    )
                    conn.commit()
                return

            if path_exam and path_answer:
                async with stats_lock:
                    stats.successful_exams += 1
                    if path_exam and isinstance(path_exam, str):
                        stats.exam_files_downloaded += 1
                    if path_answer and isinstance(path_answer, str):
                        stats.answer_files_downloaded += 1

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
            else:
                async with stats_lock:
                    stats.failed_exams += 1

    except Exception as e:
        print(f"💥 Erro fatal inesperado no ID {exam_id}: {e}")
        async with stats_lock:
            stats.failed_exams += 1


def format_summary(stats: DownloadStats) -> str:
    """Formata o sumário de downloads em um formato legível"""
    separator = "=" * 80

    summary = f"""
{separator}
DOWNLOAD SESSION SUMMARY
{separator}
Total exams processed: {stats.total_exams}
Successful exams: {stats.successful_exams}
Failed exams: {stats.failed_exams}
Unauthorized exams: {stats.unauthorized_exams}
Exam files downloaded: {stats.exam_files_downloaded}
Answer files downloaded: {stats.answer_files_downloaded}
Total files downloaded: {stats.total_files_downloaded}
Duration: {stats.duration}
Average time per exam: {stats.average_time_per_exam}
{separator}
"""
    return summary


def save_summary_to_log(stats: DownloadStats) -> None:
    """Salva o sumário de downloads no arquivo de log"""
    log_path = os.path.join(LOG_DIR, LOG_FILE)

    summary = format_summary(stats)

    # Adicionar timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"\n[{timestamp}]\n{summary}\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log_entry)

    # Também salvar como JSON no final do arquivo para futura análise
    stats_dict = asdict(stats)
    stats_dict["start_time"] = (
        stats.start_time.isoformat() if stats.start_time else None
    )
    stats_dict["end_time"] = stats.end_time.isoformat() if stats.end_time else None
    stats_dict["duration_seconds"] = stats.duration.total_seconds()
    stats_dict["average_time_per_exam_seconds"] = (
        stats.average_time_per_exam.total_seconds()
    )



async def main():
    stats = DownloadStats()
    stats.start_time = datetime.now()
    stats_lock = asyncio.Lock()

    conn = sqlite3.connect(DB_NAME)

    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, banca, ano, instituicao, cargo, prova_url, gabarito_url FROM concursos WHERE status_download = 'pendente'"
    )
    rows = cursor.fetchall()

    if not rows:
        print("🏁 Nada para baixar!")
        stats.end_time = datetime.now()
        save_summary_to_log(stats)
        return

    stats.total_exams = len(rows)

    semaphore = asyncio.Semaphore(CONCURRENT_DOWNLOADS)
    db_lock = asyncio.Lock()

    limits = httpx.Limits(max_connections=CONCURRENT_DOWNLOADS + 5)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [
            process_row(client, row, semaphore, conn, db_lock, stats, stats_lock)
            for row in rows
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    conn.close()

    stats.end_time = datetime.now()

    # Exibir sumário na tela
    print(format_summary(stats))

    # Salvar sumário no arquivo de log
    save_summary_to_log(stats)


if __name__ == "__main__":
    asyncio.run(main())
