from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from google import genai

# Modelo solicitado
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"


@dataclass
class AIResult:
    success: bool
    content: Optional[Dict[str, Any]]
    raw_text: str
    attempts: int
    error: Optional[str] = None


class RateLimitError(RuntimeError):
    pass


class DailyLimitReachedError(RuntimeError):
    pass


class SlidingWindowRateLimiter:
    """
    Rate limiter thread-safe para:
    - max_per_minute (janela móvel de 60s)
    - max_per_day (janela móvel de 24h)
    """

    def __init__(self, max_per_minute: int = 10, max_per_day: int = 500):
        self.max_per_minute = max_per_minute
        self.max_per_day = max_per_day

        self._lock = threading.Lock()
        self._minute_events: List[float] = []
        self._day_events: List[float] = []

    def _cleanup(self, now_ts: float) -> None:
        minute_threshold = now_ts - 60.0
        day_threshold = now_ts - 86400.0

        self._minute_events = [
            ts for ts in self._minute_events if ts >= minute_threshold
        ]
        self._day_events = [ts for ts in self._day_events if ts >= day_threshold]

    def acquire(self, timeout_seconds: float = 120.0) -> None:
        """
        Bloqueia até haver vaga para executar uma requisição ou lança erro por timeout.
        Se limite diário já foi atingido dentro da janela móvel, lança DailyLimitReachedError.
        """
        start = time.time()

        while True:
            with self._lock:
                now_ts = time.time()
                self._cleanup(now_ts)

                if len(self._day_events) >= self.max_per_day:
                    raise DailyLimitReachedError(
                        f"Limite diário de {self.max_per_day} requisições já atingido."
                    )

                if len(self._minute_events) < self.max_per_minute:
                    self._minute_events.append(now_ts)
                    self._day_events.append(now_ts)
                    return

                oldest_in_minute = (
                    min(self._minute_events) if self._minute_events else now_ts
                )
                wait_for = max(0.05, 60.0 - (now_ts - oldest_in_minute))

            if (time.time() - start) > timeout_seconds:
                raise RateLimitError(
                    f"Timeout aguardando rate-limit ({self.max_per_minute}/min)."
                )
            time.sleep(min(wait_for, 1.0))


def load_prompt(prompt_path: str) -> str:
    path = Path(prompt_path)
    if not path.exists():
        raise FileNotFoundError(f"Prompt não encontrado: {prompt_path}")
    return path.read_text(encoding="utf-8")


def load_json_schema(schema_path: str = "config/json_model.json") -> Dict[str, Any]:
    path = Path(schema_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de modelo JSON não encontrado: {schema_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json_from_text(text: str) -> str:
    stripped = text.strip()

    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        return stripped[first : last + 1].strip()

    return stripped


def _normalize_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    if "questões" in data and "questoes" not in data:
        data["questoes"] = data.pop("questões")
    return data


def _validate_prova_payload(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    keys = set(payload.keys())
    has_questoes = ("questoes" in keys) or ("questões" in keys)
    has_discursivas = "discursivas" in keys

    if not has_questoes:
        return False, "JSON inválido (prova): chave 'questoes' (ou 'questões') ausente."
    if not has_discursivas:
        return False, "JSON inválido (prova): chave 'discursivas' ausente."

    if "questoes" in payload and not isinstance(payload["questoes"], list):
        return False, "JSON inválido (prova): 'questoes' deve ser lista."
    if "discursivas" in payload and not isinstance(payload["discursivas"], list):
        return False, "JSON inválido (prova): 'discursivas' deve ser lista."

    return True, None


def _validate_gabarito_payload(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if "metadata_identificada" not in payload:
        return False, "JSON inválido (gabarito): chave 'metadata_identificada' ausente."
    if "gabarito_oficial" not in payload:
        return False, "JSON inválido (gabarito): chave 'gabarito_oficial' ausente."

    md = payload.get("metadata_identificada")
    go = payload.get("gabarito_oficial")

    if not isinstance(md, dict):
        return (
            False,
            "JSON inválido (gabarito): 'metadata_identificada' deve ser objeto.",
        )
    if not isinstance(go, dict):
        return False, "JSON inválido (gabarito): 'gabarito_oficial' deve ser objeto."

    for required in (
        "cargo_identificado_na_capa",
        "tipo_ou_cor_identificado",
        "match_encontrado_no_gabarito",
    ):
        if required not in md:
            return False, f"JSON inválido (gabarito): metadata sem '{required}'."

    if not isinstance(md.get("match_encontrado_no_gabarito"), bool):
        return (
            False,
            "JSON inválido (gabarito): 'match_encontrado_no_gabarito' deve ser boolean.",
        )

    valid_values = {"A", "B", "C", "D", "E", "anulada"}
    for q_num, ans in go.items():
        if not isinstance(q_num, str):
            return False, "JSON inválido (gabarito): as chaves devem ser string."
        if not re.fullmatch(r"\d+", q_num):
            return (
                False,
                f"JSON inválido (gabarito): chave de questão inválida '{q_num}'.",
            )
        if not isinstance(ans, str):
            return (
                False,
                f"JSON inválido (gabarito): resposta da questão {q_num} deve ser string.",
            )
        if ans not in valid_values:
            return (
                False,
                f"JSON inválido (gabarito): resposta '{ans}' inválida na questão {q_num}.",
            )

    return True, None


def _file_part(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo não encontrado para envio ao modelo: {path}")

    suffix = p.suffix.lower()
    mime = "application/octet-stream"
    if suffix == ".pdf":
        mime = "application/pdf"
    elif suffix in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif suffix == ".png":
        mime = "image/png"
    elif suffix == ".webp":
        mime = "image/webp"

    return genai.types.Part.from_bytes(data=p.read_bytes(), mime_type=mime)


def _build_user_instruction(prompt_text: str, image_file_names: List[str]) -> str:
    images_list = (
        "\n".join(f"- {name}" for name in image_file_names)
        if image_file_names
        else "- (sem imagens listadas)"
    )
    return (
        f"{prompt_text.strip()}\n\n"
        "INSTRUÇÕES ADICIONAIS DO PIPELINE:\n"
        "1) Responda apenas com JSON puro (sem markdown).\n"
        "2) Siga estritamente o modelo esperado no prompt.\n"
        "3) Lista de imagens disponíveis para referência:\n"
        f"{images_list}\n"
    )


def _genai_generate_with_parts(
    client: genai.Client,
    model: str,
    prompt_text: str,
    attachments: List[str],
    listed_image_names: List[str],
    logger: logging.Logger,
) -> str:
    contents: List[Any] = [_build_user_instruction(prompt_text, listed_image_names)]

    for file_path in attachments:
        try:
            contents.append(_file_part(file_path))
        except Exception as exc:
            logger.warning("Falha ao anexar arquivo '%s' ao prompt: %s", file_path, exc)

    resp = client.models.generate_content(
        model=model,
        contents=contents,
    )

    text = getattr(resp, "text", None)
    if text and text.strip():
        return text.strip()

    chunks: List[str] = []
    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            ptxt = getattr(part, "text", None)
            if ptxt:
                chunks.append(ptxt)

    if chunks:
        return "\n".join(chunks).strip()

    raise RuntimeError("Resposta vazia da IA.")


def _call_ai_core(
    *,
    attachments: List[str],
    listed_image_names: List[str],
    prompt_path: str,
    schema_path: str,
    model: str,
    network_retries: int,
    invalid_json_extra_retry: int,
    backoff_base_seconds: float,
    rate_limiter: Optional[SlidingWindowRateLimiter],
    logger: Optional[logging.Logger],
    validation_mode: str,  # "prova" | "gabarito"
) -> AIResult:
    if logger is None:
        logger = logging.getLogger(__name__)

    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return AIResult(
            success=False,
            content=None,
            raw_text="",
            attempts=0,
            error="Chave de API ausente (.env: GEMINI_API_KEY ou GOOGLE_API_KEY).",
        )

    _ = load_json_schema(schema_path)
    prompt_text = load_prompt(prompt_path)
    client = genai.Client(api_key=api_key)

    attempts = 0
    json_invalid_retries_used = 0
    max_total_attempts = network_retries + 1 + invalid_json_extra_retry

    while attempts < max_total_attempts:
        attempts += 1
        try:
            if rate_limiter is not None:
                rate_limiter.acquire(timeout_seconds=180.0)

            raw = _genai_generate_with_parts(
                client=client,
                model=model,
                prompt_text=prompt_text,
                attachments=attachments,
                listed_image_names=listed_image_names,
                logger=logger,
            )

            payload = json.loads(_extract_json_from_text(raw))
            payload = _normalize_keys(payload)

            if validation_mode == "prova":
                valid, reason = _validate_prova_payload(payload)
            elif validation_mode == "gabarito":
                valid, reason = _validate_gabarito_payload(payload)
            else:
                return AIResult(
                    success=False,
                    content=None,
                    raw_text=raw,
                    attempts=attempts,
                    error=f"Modo de validação desconhecido: {validation_mode}",
                )

            if valid:
                return AIResult(
                    success=True,
                    content=payload,
                    raw_text=raw,
                    attempts=attempts,
                    error=None,
                )

            if json_invalid_retries_used < invalid_json_extra_retry:
                json_invalid_retries_used += 1
                sleep_for = backoff_base_seconds * (
                    2 ** (json_invalid_retries_used - 1)
                )
                logger.warning(
                    "JSON inválido (modo=%s, tentativa %s/%s): %s. Repetindo após %.1fs.",
                    validation_mode,
                    json_invalid_retries_used,
                    invalid_json_extra_retry,
                    reason,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            return AIResult(
                success=False,
                content=None,
                raw_text=raw,
                attempts=attempts,
                error=reason or f"JSON inválido após retries (modo={validation_mode}).",
            )

        except json.JSONDecodeError as exc:
            if json_invalid_retries_used < invalid_json_extra_retry:
                json_invalid_retries_used += 1
                sleep_for = backoff_base_seconds * (
                    2 ** (json_invalid_retries_used - 1)
                )
                logger.warning(
                    "Falha de parse JSON (modo=%s, tentativa %s/%s): %s. Repetindo após %.1fs.",
                    validation_mode,
                    json_invalid_retries_used,
                    invalid_json_extra_retry,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            return AIResult(
                success=False,
                content=None,
                raw_text="",
                attempts=attempts,
                error=f"JSON inválido após retries (modo={validation_mode}): {exc}",
            )

        except DailyLimitReachedError as exc:
            return AIResult(
                success=False,
                content=None,
                raw_text="",
                attempts=attempts,
                error=str(exc),
            )

        except Exception as exc:
            if attempts <= network_retries:
                sleep_for = backoff_base_seconds * (2 ** (attempts - 1))
                logger.warning(
                    "Falha na chamada da IA (modo=%s, tentativa %s/%s): %s. Retry em %.1fs.",
                    validation_mode,
                    attempts,
                    network_retries + 1,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            return AIResult(
                success=False,
                content=None,
                raw_text="",
                attempts=attempts,
                error=f"Falha na IA após retries (modo={validation_mode}): {exc}",
            )

    return AIResult(
        success=False,
        content=None,
        raw_text="",
        attempts=attempts,
        error=f"Falha desconhecida após tentativas (modo={validation_mode}).",
    )


def call_ai_prova_with_retries(
    prova_pdf_path: str,
    extracted_image_paths: List[str],
    prompt_path: str = "config/prompt_prova.md",
    schema_path: str = "config/json_model.json",
    model: str = GEMINI_MODEL,
    network_retries: int = 3,
    invalid_json_extra_retry: int = 1,
    backoff_base_seconds: float = 2.0,
    rate_limiter: Optional[SlidingWindowRateLimiter] = None,
    logger: Optional[logging.Logger] = None,
) -> AIResult:
    attachments: List[str] = [prova_pdf_path, *extracted_image_paths]
    listed_image_names = [Path(p).name for p in extracted_image_paths]

    return _call_ai_core(
        attachments=attachments,
        listed_image_names=listed_image_names,
        prompt_path=prompt_path,
        schema_path=schema_path,
        model=model,
        network_retries=network_retries,
        invalid_json_extra_retry=invalid_json_extra_retry,
        backoff_base_seconds=backoff_base_seconds,
        rate_limiter=rate_limiter,
        logger=logger,
        validation_mode="prova",
    )


def call_ai_gabarito_with_retries(
    gabarito_pdf_path: str,
    prova_capa_image_path: str,
    prompt_path: str = "config/prompt_gabarito.md",
    schema_path: str = "config/json_model.json",
    model: str = GEMINI_MODEL,
    network_retries: int = 3,
    invalid_json_extra_retry: int = 1,
    backoff_base_seconds: float = 2.0,
    rate_limiter: Optional[SlidingWindowRateLimiter] = None,
    logger: Optional[logging.Logger] = None,
) -> AIResult:
    attachments: List[str] = [gabarito_pdf_path, prova_capa_image_path]
    listed_image_names = [Path(prova_capa_image_path).name]

    return _call_ai_core(
        attachments=attachments,
        listed_image_names=listed_image_names,
        prompt_path=prompt_path,
        schema_path=schema_path,
        model=model,
        network_retries=network_retries,
        invalid_json_extra_retry=invalid_json_extra_retry,
        backoff_base_seconds=backoff_base_seconds,
        rate_limiter=rate_limiter,
        logger=logger,
        validation_mode="gabarito",
    )


# Compatibilidade retroativa com chamadas antigas do projeto
def call_ai_with_retries(
    prova_pdf_path: str,
    gabarito_pdf_path: str,
    extracted_image_paths: List[str],
    prompt_path: str = "config/prompt.md",
    schema_path: str = "config/json_model.json",
    model: str = GEMINI_MODEL,
    network_retries: int = 3,
    invalid_json_extra_retry: int = 1,
    backoff_base_seconds: float = 2.0,
    rate_limiter: Optional[SlidingWindowRateLimiter] = None,
    logger: Optional[logging.Logger] = None,
) -> AIResult:
    attachments: List[str] = [prova_pdf_path, gabarito_pdf_path, *extracted_image_paths]
    listed_image_names = [Path(p).name for p in extracted_image_paths]

    return _call_ai_core(
        attachments=attachments,
        listed_image_names=listed_image_names,
        prompt_path=prompt_path,
        schema_path=schema_path,
        model=model,
        network_retries=network_retries,
        invalid_json_extra_retry=invalid_json_extra_retry,
        backoff_base_seconds=backoff_base_seconds,
        rate_limiter=rate_limiter,
        logger=logger,
        validation_mode="prova",
    )
