import sqlite3

DB_NAME = "mentoria.db"
CSV_PATH = "provas.csv"

def initialize_db():
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
            data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            questoes_path TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS questoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enunciado TEXT
        )
    ''')
    
    conn.commit()
    return conn

if __name__ == "__main__":
    initialize_db()
    