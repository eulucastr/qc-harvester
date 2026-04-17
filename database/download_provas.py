import sqlite3
import asyncio
import httpx
import os
import re

# Configurações
DB_NAME = "mentoria-provas.db"
BASE_DIR = "D:/mentor.ia"
CONCURRENT_DOWNLOADS = 5 # Quantos downloads simultâneos (não exagere para não ser bloqueado)

# Criar as pastas de destino
os.makedirs(f"{BASE_DIR}/provas", exist_ok=True)
os.makedirs(f"{BASE_DIR}/gabaritos", exist_ok=True)

def limpar_nome_arquivo(nome):
    """Remove caracteres que o Windows proíbe em nomes de arquivos"""
    return re.sub(r'[\\/*?:"<>|]', "_", str(nome))
    
async def baixar_ficheiro(client, url, pasta, id_prova, tipo, nome_arquivo):
    if not url or str(url).strip() == "" or str(url) == 'nan':
        return False
    
    os.makedirs(pasta, exist_ok=True)
    
    extensao = ".pdf" # A maioria é PDF
    nome_limpo = limpar_nome_arquivo(nome_arquivo)
    nome_ficheiro = f"{id_prova}_{tipo}_{nome_limpo}{extensao}"
    caminho_final = os.path.join(pasta, nome_ficheiro)

    try:
        response = await client.get(url, timeout=30.0, follow_redirects=True)
        if response.status_code == 200:
            with open(caminho_final, 'wb') as f:
                f.write(response.content)
            return caminho_final 
        else:
            print(f"⚠️ Erro {response.status_code} ao baixar {tipo} {id_prova}")
            return False
    except Exception as e:
        print(f"❌ Erro na conexão {tipo} {id_prova}: {e}")
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
        
        path_p = await baixar_ficheiro(client, url_p, pasta_prova, id_prova, "prova", nome_base)
        path_g = await baixar_ficheiro(client, url_g, pasta_gabarito, id_prova, "gabarito", nome_base)

        if path_p and path_g:
            cursor = conn.cursor()
            # Atualizamos o status E salvamos o caminho onde o arquivo foi parar (importante para o passo 3)
            cursor.execute(
                "UPDATE concursos SET status_download = 'concluido', prova_path = ?, gabarito_path = ? WHERE id = ?", 
                (path_p, path_g, id_prova)
            )
            conn.commit()
            print(f"✅ ID {id_prova} finalizado.")

async def main():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Busca apenas o que ainda não foi baixado
    cursor.execute("SELECT id, banca, ano, instituicao, cargo, prova_url, gabarito_url FROM concursos WHERE status_download = 'pendente'")
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