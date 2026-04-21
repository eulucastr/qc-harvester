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

# Modelo solicitado pelo usuário
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

                # calcular quanto falta para liberar 1 slot na janela de 60s
                oldest_in_minute = (
                    min(self._minute_events) if self._minute_events else now_ts
                )
                wait_for = max(0.05, 60.0 - (now_ts - oldest_in_minute))

            if (time.time() - start) > timeout_seconds:
                raise RateLimitError(
                    f"Timeout aguardando rate-limit ({self.max_per_minute}/min)."
                )
            time.sleep(min(wait_for, 1.0))


def load_prompt(prompt_path: str = "config/prompt.md") -> str:
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
    """
    Tenta extrair JSON de uma resposta possivelmente envelopada com markdown.
    """
    stripped = text.strip()

    # remove code fences comuns
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)

    # tenta detectar primeiro objeto JSON
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = stripped[first : last + 1]
        return candidate.strip()

    return stripped


def _basic_shape_validation(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validação mínima compatível com o arquivo de modelo atual (exemplo, não JSON Schema formal):
    - precisa ter chaves: 'questões'/'questoes' e 'discursivas'
    """
    keys = set(payload.keys())
    has_questoes = ("questoes" in keys) or ("questões" in keys)
    has_discursivas = "discursivas" in keys

    if not has_questoes:
        return False, "JSON inválido: chave 'questoes' (ou 'questões') ausente."
    if not has_discursivas:
        return False, "JSON inválido: chave 'discursivas' ausente."

    return True, None


def _normalize_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza para chave sem acento para facilitar restante da pipeline.
    """
    if "questões" in payload and "questoes" not in payload:
        payload["questoes"] = payload.pop("questões")
    return payload


def _file_part(path: str) -> Any:
    """
    Cria parte de arquivo para SDK google-genai.
    """
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


def _build_user_instruction(
    prompt_text: str,
    image_file_names: List[str],
) -> str:
    images_list = (
        "\n".join(f"- {name}" for name in image_file_names)
        if image_file_names
        else "- (sem imagens extraídas)"
    )
    return (
        f"{prompt_text.strip()}\n\n"
        "INSTRUÇÕES ADICIONAIS DO PIPELINE:\n"
        "1) Responda apenas com JSON puro (sem markdown).\n"
        "2) Siga estritamente o modelo esperado.\n"
        "3) Lista de imagens extraídas disponíveis para referência:\n"
        f"{images_list}\n"
    )


def _genai_generate(
    client: genai.Client,
    model: str,
    prompt_text: str,
    prova_pdf_path: str,
    gabarito_pdf_path: str,
    extracted_image_paths: List[str],
    logger: logging.Logger,
) -> str:
    image_names = [Path(p).name for p in extracted_image_paths]

    contents: List[Any] = [
        _build_user_instruction(prompt_text, image_names),
        _file_part(prova_pdf_path),
        _file_part(gabarito_pdf_path),
    ]

    for img_path in extracted_image_paths:
        try:
            contents.append(_file_part(img_path))
        except Exception as exc:
            logger.warning("Falha ao anexar imagem '%s' ao prompt: %s", img_path, exc)

    resp = client.models.generate_content(
        model=model,
        contents=contents,
    )

    text = getattr(resp, "text", None)
    if text and text.strip():
        return text.strip()

    # fallback: concatena partes textuais se existir estrutura diferente
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
    """
    Regras implementadas:
    1) Falha de rede/rate-limit/timeout -> retry com backoff (network_retries).
    2) Respondeu JSON inválido -> 1 retry adicional (invalid_json_extra_retry=1).
    3) Se continuar inválido -> retorna success=False para manter status 'pendente'.
    """
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

    _ = load_json_schema(schema_path)  # reservado para validações futuras mais estritas
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

            raw = _genai_generate(
                client=client,
                model=model,
                prompt_text=prompt_text,
                prova_pdf_path=prova_pdf_path,
                gabarito_pdf_path=gabarito_pdf_path,
                extracted_image_paths=extracted_image_paths,
                logger=logger,
            )

            json_text = _extract_json_from_text(raw)
            payload = json.loads(json_text)
            payload = _normalize_keys(payload)

            valid, reason = _basic_shape_validation(payload)
            if valid:
                return AIResult(
                    success=True,
                    content=payload,
                    raw_text=raw,
                    attempts=attempts,
                    error=None,
                )

            # JSON parseou, mas inválido no shape:
            if json_invalid_retries_used < invalid_json_extra_retry:
                json_invalid_retries_used += 1
                sleep_for = backoff_base_seconds * (
                    2 ** (json_invalid_retries_used - 1)
                )
                logger.warning(
                    "JSON inválido (tentativa %s/%s): %s. Repetindo após %.1fs.",
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
                error=reason or "JSON inválido após retries.",
            )

        except (json.JSONDecodeError,) as exc:
            # Tratamos como "JSON inválido", com 1 retry adicional
            if json_invalid_retries_used < invalid_json_extra_retry:
                json_invalid_retries_used += 1
                sleep_for = backoff_base_seconds * (
                    2 ** (json_invalid_retries_used - 1)
                )
                logger.warning(
                    "Falha de parse JSON (tentativa %s/%s): %s. Repetindo após %.1fs.",
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
                error=f"JSON inválido após retries: {exc}",
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
            # Falhas transitórias de rede/timeout/rate-limit do provider -> retry com backoff
            # Se atingir limite de tentativas, encerra.
            if attempts <= network_retries:
                sleep_for = backoff_base_seconds * (2 ** (attempts - 1))
                logger.warning(
                    "Falha na chamada da IA (tentativa %s/%s): %s. Retry em %.1fs.",
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
                error=f"Falha na IA após retries: {exc}",
            )

    return AIResult(
        success=False,
        content=None,
        raw_text="",
        attempts=attempts,
        error="Falha desconhecida após tentativas.",
    )
