import sqlite3
import pandas as pd

DB_NAME = "mentoria-provas.db"
CSV_PATH = "provas.csv"

def inicializar_banco():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    print("🛠️ Criando tabelas...")
    
    # UNIQUE(url_prova) evita que o mesmo link seja inserido duas vezes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS concursos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            banca TEXT,
            ano INTEGER,
            instituicao TEXT,
            estado TEXT,
            cargo TEXT,
            especialidade TEXT,
            aplicacao TEXT,
            escolaridade TEXT,
            prova_url TEXT UNIQUE,
            gabarito_url TEXT,
            alteracoes_url TEXT,
            edital_url TEXT,
            status_download TEXT DEFAULT 'pendente',
            status_extracao TEXT DEFAULT 'pendente',
            prova_path TEXT,
            gabarito_path TEXT,
            prova_md TEXT,
            gabarito_md TEXT,
            data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    return conn

def carregar_dados_csv(conn):
    print(f"📄 Lendo dados de {CSV_PATH}...")
    try:
        df = pd.read_csv(CSV_PATH)
        
        cursor = conn.cursor()
        for _, row in df.iterrows():
            try:
                cursor.execute('''
                    INSERT INTO concursos 
                    (banca, ano, instituicao, estado, cargo, especialidade, escolaridade, prova_url, gabarito_url, alteracoes_url, edital_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    row['banca'], row['ano'], row['instituição'], row['estado'], row['cargo'], 
                    row['especialidade'], row['escolaridade'], row['prova'], 
                    row['gabarito'], row['alterações'], row['edital']
                ))
            except sqlite3.IntegrityError:
                # Se a URL já existir, o SQLite pula essa linha automaticamente
                continue
        
        conn.commit()
        print("✅ Sucesso! Dados carregados na fila de processamento.")
        
    except Exception as e:
        print(f"❌ Erro ao carregar CSV: {e}")

if __name__ == "__main__":
    conexao = inicializar_banco()
    carregar_dados_csv(conexao)
    conexao.close()