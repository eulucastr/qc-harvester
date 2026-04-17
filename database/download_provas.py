import asyncio
import os
import random
import re
import sqlite3
import time

import httpx

# Configurações
DB_NAME = "mentoria-provas.db"
BASE_DIR = "D:/mentor.ia"
CONCURRENT_DOWNLOADS = (
    5  # Quantos downloads simultâneos (não exagere para não ser bloqueado)
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


def limpar_nome_arquivo(nome):
    """Remove caracteres que o Windows proíbe em nomes de arquivos"""
    return re.sub(r'[\\/*?:\"<>|]', "_", str(nome)).lower()


def obter_user_agent_aleatorio():
    """Retorna um User-Agent aleatório para simular navegadores diferentes"""
    return random.choice(USER_AGENTS)


async def sleep_aleatorio():
    """Espera um tempo aleatório entre 3 e 7 segundos para não parecer robótico"""
    delay = random.uniform(3, 7)
    await asyncio.sleep(delay)


async def baixar_ficheiro(
    client, url, pasta, id_prova, tipo, nome_arquivo, max_retries=3
):
    if not url or str(url).strip() == "" or str(url) == "nan":
        return False

    os.makedirs(pasta, exist_ok=True)

    extensao = ".pdf"
    nome_limpo = limpar_nome_arquivo(nome_arquivo)
    nome_ficheiro = f"{id_prova}_{tipo}_{nome_limpo}{extensao}"
    caminho_final = os.path.join(pasta, nome_ficheiro)

    # Backoff exponencial: começa em 1 minuto (60 segundos)
    delay_base = 60

    for tentativa in range(max_retries):
        try:
            # User-Agent aleatório para cada requisição
            headers = {"User-Agent": obter_user_agent_aleatorio()}

            response = await client.get(
                url, headers=headers, timeout=30.0, follow_redirects=True
            )

            if response.status_code == 200:
                with open(caminho_final, "wb") as f:
                    f.write(response.content)
                print(f"✅ {tipo} {id_prova} baixado com sucesso")
                return caminho_final
            else:
                print(f"⚠️ Erro {response.status_code} ao baixar {tipo} {id_prova}")
                return False

        except Exception as e:
            tentativa_atual = tentativa + 1
            print(
                f"❌ Erro na conexão {tipo} {id_prova}: {e} (tentativa {tentativa_atual}/{max_retries})"
            )

            # Se não for a última tentativa, aplicar backoff exponencial
            if tentativa_atual < max_retries:
                delay = delay_base * (2**tentativa)  # 1 min, 2 min, 4 min...
                print(f"⏳ Aguardando {delay}s antes de tentar novamente...")
                await asyncio.sleep(delay)

    print(f"❌ Falha ao baixar {tipo} {id_prova} após {max_retries} tentativas")
    return False


async def processar_linha(client, row, semaphore, conn):
    id_prova, banca, ano, instituicao, cargo, url_p, url_g = row

    # O Semaphore controla quantos downloads ocorrem ao mesmo tempo
    async with semaphore:
        print(f"🚀 Iniciando ID {id_prova}...")

        # Criamos um nome base limpo
        nome_base = f"{instituicao}_{cargo}_{ano}"

        # Definimos as pastas incluindo a banca
        pasta_prova = os.path.join(BASE_DIR, "provas", limpar_nome_arquivo(banca))
        pasta_gabarito = os.path.join(BASE_DIR, "gabaritos", limpar_nome_arquivo(banca))

        path_p = await baixar_ficheiro(
            client, url_p, pasta_prova, id_prova, "prova", nome_base
        )

        # Sleep aleatório entre downloads de prova e gabarito
        await sleep_aleatorio()

        path_g = await baixar_ficheiro(
            client, url_g, pasta_gabarito, id_prova, "gabarito", nome_base
        )

        if path_p and path_g:
            cursor = conn.cursor()
            # Atualizamos o status E salvamos o caminho onde o arquivo foi parar
            cursor.execute(
                "UPDATE concursos SET status_download = 'concluido', prova_path = ?, gabarito_path = ? WHERE id = ?",
                (path_p, path_g, id_prova),
            )
            conn.commit()
            print(f"✅ ID {id_prova} finalizado.")


async def main():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Busca apenas o que ainda não foi baixado
    cursor.execute(
        "SELECT id, banca, ano, instituicao, cargo, prova_url, gabarito_url FROM concursos WHERE status_download = 'pendente'"
    )
    rows = cursor.fetchall()

    if not rows:
        print("🏁 Nada para baixar!")
        return

    semaphore = asyncio.Semaphore(CONCURRENT_DOWNLOADS)

    async with httpx.AsyncClient() as client:
        tasks = [processar_linha(client, row, semaphore, conn) for row in rows]
        await asyncio.gather(*tasks)

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
