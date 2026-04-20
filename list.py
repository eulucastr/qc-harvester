from google import genai
from dotenv import load_dotenv
import os
# ──────────────────────────────────────────────
# Configuração
# ──────────────────────────────────────────────

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=API_KEY)

for m in client.models.list():
    for action in m.supported_actions:
        if action == "generateContent":
            print(m.name)