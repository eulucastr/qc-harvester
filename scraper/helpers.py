# Siglas dos estados brasileiros
ESTADOS_BRASILEIROS = {
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
}

def is_estado(text: str) -> bool:
    """Verifica se o texto contém uma sigla de estado brasileiro ou a string região (para casos como "1a Região ")"""
    
    return any(estado in text for estado in ESTADOS_BRASILEIROS) or "região" in text.lower()

def parse_title_parts(title_parts: list) -> dict:
    """
    Extrai e classifica as partes do título de uma prova.

    Estrutura esperada (separado por " - "):
    - 3 partes: banca, ano, instituição
    - 4 partes: banca, ano, instituição, especialidade
    - 5 partes: banca, ano, instituição, (estado|cargo), especialidade
    - Se a 4ª é um estado: banca, ano, instituição, estado, especialidade
    - Se a 4ª não é estado: banca, ano, instituição, cargo, especialidade
    - 6+ partes: banca, ano, instituição, estado, cargo, especialidade

    Args:
        title_parts: Lista de strings com as partes do título

    Returns:
        dict com chaves: banca, ano, instituição, estado (opcional),
                        cargo (opcional), especialidade (opcional)
    """
    result = {}

    if len(title_parts) < 3:
        return result

    result["banca"] = title_parts[0]
    result["ano"] = title_parts[1]
    result["instituição"] = title_parts[2]

    if len(title_parts) == 4:
        # banca, ano, instituição, especialidade
        resultado_cargo = title_parts[3].replace("Função:", "").strip()
        if resultado_cargo:
            result["cargo"] = resultado_cargo

    elif len(title_parts) == 5:
        # Checar se a 4ª parte é um estado
        if is_estado(title_parts[3]):
            # banca, ano, instituição, estado, especialidade
            result["estado"] = title_parts[3]
            resultado_cargo = title_parts[4].replace("Função:", "").strip()
            if resultado_cargo:
                result["cargo"] = resultado_cargo
        else:
            # banca, ano, instituição, cargo, especialidade
            result["cargo"] = title_parts[3]
            resultado_especialidade = title_parts[4].replace("Função:", "").strip()
            if resultado_especialidade:
                result["especialidade"] = resultado_especialidade

    elif len(title_parts) >= 6:
        # banca, ano, instituição, estado, cargo, especialidade
        
        if is_estado(title_parts[3]):
            result["estado"] = title_parts[3]
            result["cargo"] = title_parts[4]
            resultado_especialidade = title_parts[5].replace("Função:", "").strip()
            if resultado_especialidade:
                result["especialidade"] = resultado_especialidade
        else:
            result["cargo"] = title_parts[3]
            resultado_especialidade = f"{title_parts[4]} - {title_parts[5]}".replace("Função:", "").strip()
            result["especialidade"] = resultado_especialidade

    return result
