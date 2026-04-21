import sqlite3
import pandas as pd

DB_NAME = "mentoria.db"
CSV_PATH = "provas.csv"

def load_csv_data(conn):
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
                # Se a URL de prova já existir, o SQLite pula essa linha automaticamente
                continue
        
        conn.commit()
        print("✅ Sucesso! Dados carregados na fila de processamento.")
        
    except Exception as e:
        print(f"❌ Erro ao carregar CSV: {e}")
        
if __name__ == "__main__":
    conn = sqlite3.connect(DB_NAME)
    load_csv_data(conn)
    conn.close()