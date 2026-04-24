from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


class MergeGabaritoError(ValueError):
    """Erro de validação/consistência no merge de questões com gabarito."""


@dataclass
class MergeStats:
    total_objetivas: int
    total_discursivas: int
    total_com_gabarito: int
    total_sem_gabarito: int
    total_anuladas: int


def _normalize_questoes_key(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    if "questões" in data and "questoes" not in data:
        data["questoes"] = data.pop("questões")
    return data


def _normalize_numero(value: Any) -> str:
    if value is None:
        raise MergeGabaritoError("Número da questão ausente.")
    text = str(value).strip()
    if not text:
        raise MergeGabaritoError("Número da questão vazio.")
    # remove sufixo ".0" comum quando número veio como float serializado
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _normalize_gabarito_value(value: Any) -> str:
    if value is None:
        raise MergeGabaritoError("Valor de gabarito ausente.")

    raw = str(value).strip()
    if not raw:
        raise MergeGabaritoError("Valor de gabarito vazio.")

    low = raw.lower()
    if low in {"anulada", "anulado", "nula", "nulo", "x", "*"}:
        return "anulada"

    up = raw.upper()
    if up in {"A", "B", "C", "D", "E"}:
        return up

    # Certo/Errado
    if up in {"CERTO", "C"}:
        return "C"
    if up in {"ERRADO", "E"}:
        return "E"

    raise MergeGabaritoError(f"Valor de gabarito inválido: {raw}")


def _validate_payload_questoes(payload_questoes: Dict[str, Any]) -> Dict[str, Any]:
    data = _normalize_questoes_key(payload_questoes)

    if not isinstance(data, dict):
        raise MergeGabaritoError("Payload de questões deve ser um objeto JSON.")

    if "questoes" not in data:
        raise MergeGabaritoError(
            "Payload de questões sem chave 'questoes' (ou 'questões')."
        )
    if "discursivas" not in data:
        raise MergeGabaritoError("Payload de questões sem chave 'discursivas'.")

    if not isinstance(data["questoes"], list):
        raise MergeGabaritoError("'questoes' deve ser uma lista.")
    if not isinstance(data["discursivas"], list):
        raise MergeGabaritoError("'discursivas' deve ser uma lista.")

    for i, q in enumerate(data["questoes"], start=1):
        if not isinstance(q, dict):
            raise MergeGabaritoError(f"questoes[{i}] deve ser um objeto.")
        if "numero" not in q:
            raise MergeGabaritoError(f"questoes[{i}] sem chave obrigatória 'numero'.")
        if "enunciado" not in q:
            raise MergeGabaritoError(
                f"questoes[{i}] sem chave obrigatória 'enunciado'."
            )

    for i, d in enumerate(data["discursivas"], start=1):
        if not isinstance(d, dict):
            raise MergeGabaritoError(f"discursivas[{i}] deve ser um objeto.")
        if "numero" not in d or "enunciado" not in d:
            raise MergeGabaritoError(
                f"discursivas[{i}] sem chaves obrigatórias 'numero' e/ou 'enunciado'."
            )

    return data


def _validate_payload_gabarito(payload_gabarito: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload_gabarito, dict):
        raise MergeGabaritoError("Payload de gabarito deve ser um objeto JSON.")

    if "gabarito_oficial" not in payload_gabarito:
        raise MergeGabaritoError("Payload de gabarito sem chave 'gabarito_oficial'.")

    gabarito = payload_gabarito.get("gabarito_oficial")
    if not isinstance(gabarito, dict):
        raise MergeGabaritoError(
            "'gabarito_oficial' deve ser um objeto com numero->resposta."
        )

    normalized: Dict[str, str] = {}
    for numero, resposta in gabarito.items():
        n = _normalize_numero(numero)
        normalized[n] = _normalize_gabarito_value(resposta)

    return {
        "metadata_identificada": payload_gabarito.get("metadata_identificada", {}),
        "gabarito_oficial": normalized,
    }


def merge_questoes_com_gabarito(
    payload_questoes: Dict[str, Any],
    payload_gabarito: Dict[str, Any],
    strict_missing_gabarito: bool = False,
) -> Dict[str, Any]:
    """
    Faz o merge das questões com o gabarito oficial por número da questão.

    Regras:
    - Mantém a estrutura do payload de questões.
    - Injeta/atualiza a chave `gabarito` em cada item de `questoes`.
    - `discursivas` não recebem gabarito.
    - Se `strict_missing_gabarito=True`, lança erro se faltar gabarito para alguma objetiva.
      Caso False, deixa `gabarito=None` nas faltantes.
    """
    questoes_data = _validate_payload_questoes(payload_questoes)
    gabarito_data = _validate_payload_gabarito(payload_gabarito)

    mapa_gabarito: Dict[str, str] = gabarito_data["gabarito_oficial"]

    merged: Dict[str, Any] = dict(questoes_data)
    merged_questoes: List[Dict[str, Any]] = []

    sem_gabarito: List[str] = []
    total_anuladas = 0
    total_com_gabarito = 0

    for q in questoes_data["questoes"]:
        item = dict(q)
        numero_norm = _normalize_numero(item.get("numero"))
        resposta = mapa_gabarito.get(numero_norm)

        if resposta is None:
            if strict_missing_gabarito:
                sem_gabarito.append(numero_norm)
            item["gabarito"] = None
        else:
            item["gabarito"] = resposta
            total_com_gabarito += 1
            if resposta == "anulada":
                total_anuladas += 1

        merged_questoes.append(item)

    if sem_gabarito:
        faltantes = ", ".join(sem_gabarito)
        raise MergeGabaritoError(
            f"Faltou gabarito para {len(sem_gabarito)} questão(ões): {faltantes}"
        )

    merged["questoes"] = merged_questoes
    merged["metadata_gabarito"] = gabarito_data.get("metadata_identificada", {})

    stats = MergeStats(
        total_objetivas=len(merged_questoes),
        total_discursivas=len(questoes_data["discursivas"]),
        total_com_gabarito=total_com_gabarito,
        total_sem_gabarito=len(merged_questoes) - total_com_gabarito,
        total_anuladas=total_anuladas,
    )
    merged["merge_stats"] = {
        "total_objetivas": stats.total_objetivas,
        "total_discursivas": stats.total_discursivas,
        "total_com_gabarito": stats.total_com_gabarito,
        "total_sem_gabarito": stats.total_sem_gabarito,
        "total_anuladas": stats.total_anuladas,
    }

    return merged
