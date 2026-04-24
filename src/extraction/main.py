from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import traceback
from pathlib import Path
from shutil import rmtree
from typing import Any, Dict, List, Optional, Tuple

from .export import persist_json_and_images
from .extract_images import extract_images_from_prova, manifest_to_dict
from .use_ai import (
    SlidingWindowRateLimiter,
    call_ai_gabarito_with_retries,
    call_ai_prova_with_retries,
)

DB_PATH = "mentoria.db"
TABLE_NAME = "concursos"

PROMPT_PROVA_PATH = "config/prompt_prova.md"
PROMPT_GABARITO_PATH = "config/prompt_gabarito.md"
JSON_MODEL_PATH = "config/json_model.json"

TEMP_BASE_DIR = "temp"
OUTPUT_BASE_DIR = "D:/mentor.ia/questoes"

SUCCESS_LOG_PATH = "output/extraction.sucess.log"
ERROR_LOG_PATH = "output/extraction.error.log"

BATCH_SIZE = 5
AI_MAX_PER_MINUTE = 10
AI_MAX_PER_DAY = 500


def setup_loggers() -> Tuple[logging.Logger, logging.Logger]:
    Path("output").mkdir(parents=True, exist_ok=True)

    success_logger = logging.getLogger("extraction_success")
    error_logger = logging.getLogger("extraction_error")

    success_logger.setLevel(logging.INFO)
    error_logger.setLevel(logging.ERROR)

    if not success_logger.handlers:
        s_handler = logging.FileHandler(SUCCESS_LOG_PATH, encoding="utf-8")
        s_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        success_logger.addHandler(s_handler)

    if not error_logger.handlers:
        e_handler = logging.FileHandler(ERROR_LOG_PATH, encoding="utf-8")
        e_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        error_logger.addHandler(e_handler)

    return success_logger, error_logger


def get_db_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_pending_rows(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    query = f"""
        SELECT
            id,
            banca,
            instituicao,
            cargo,
            especialidade,
            ano,
            prova_path,
            gabarito_path,
            status_extracao,
            questoes_path
        FROM {TABLE_NAME}
        WHERE status_extracao = 'pendente'
        ORDER BY id
    """
    with get_db_connection(db_path) as conn:
        rows = conn.execute(query).fetchall()
    return [dict(row) for row in rows]


def update_row_to_extracted(
    row_id: int | str, questoes_path: str, db_path: str
) -> None:
    query = f"""
        UPDATE {TABLE_NAME}
        SET
            status_extracao = 'extraido',
            questoes_path = ?
        WHERE id = ?
    """
    with get_db_connection(db_path) as conn:
        conn.execute(query, (questoes_path, row_id))
        conn.commit()


def load_schema_like(schema_path: str = JSON_MODEL_PATH) -> Dict[str, Any]:
    p = Path(schema_path)
    if not p.exists():
        raise FileNotFoundError(f"Modelo JSON não encontrado: {schema_path}")
    return json.loads(p.read_text(encoding="utf-8"))


def ensure_local_absolute(path_value: str, field_name: str) -> str:
    p = Path(path_value)
    if not p.is_absolute():
        raise ValueError(
            f"{field_name} precisa ser caminho absoluto local: {path_value}"
        )
    if not p.exists():
        raise FileNotFoundError(f"Arquivo não encontrado em {field_name}: {path_value}")
    return str(p)


def chunked(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def cleanup_temp_dir(
    temp_dir: Optional[str], error_logger: logging.Logger, row_id: Optional[Any] = None
) -> None:
    if not temp_dir:
        print(f"[CLEANUP][id={row_id}] Nenhuma pasta temporária para limpar.")
        return
    try:
        temp_path = Path(temp_dir)
        if temp_path.exists():
            print(f"[CLEANUP][id={row_id}] Removendo pasta temporária: {temp_path}")
            rmtree(temp_path, ignore_errors=True)
            print(f"[CLEANUP][id={row_id}] Pasta temporária removida com sucesso.")
        else:
            print(f"[CLEANUP][id={row_id}] Pasta temporária não existe: {temp_path}")
    except Exception as exc:
        print(f"[CLEANUP][id={row_id}] Erro ao limpar pasta temporária: {exc}")
        error_logger.error("Falha ao limpar pasta temporária '%s': %s", temp_dir, exc)


def _build_batch_state(
    batch_rows: List[Dict[str, Any]],
) -> Dict[int | str, Dict[str, Any]]:
    state: Dict[int | str, Dict[str, Any]] = {}
    for row in batch_rows:
        row_id = row.get("id")
        if row_id is None:
            continue
        state[row_id] = {
            "row": row,
            "prova_path": None,
            "gabarito_path": None,
            "manifest": None,
            "manifest_dict": None,
            "image_paths": [],
            "first_page_pdf_path": None,
            "temp_dir": None,
            "ai_result_prova": None,
            "ai_result_gabarito": None,
            "merged_payload": None,
            "json_abs_path": None,
            "images_abs_dir": None,
            "copied_files": [],
            "status": "pendente",
            "reason": None,
        }
    return state


def _normalize_payload_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    if "questões" in data and "questoes" not in data:
        data["questoes"] = data.pop("questões")
    return data


def _parse_question_number(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except Exception:
        return None


def _normalize_gabarito_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    txt = str(value).strip()
    if not txt:
        return None

    low = txt.lower()
    if low in {"anulada", "anulado", "nula", "nulo", "x", "*"}:
        return "anulada"

    up = txt.upper()
    if up in {"A", "B", "C", "D", "E"}:
        return up

    # C/E para CEBRASPE já passa pela regra acima; aqui mantém fallback seguro
    if up in {"CERTO", "ERRADO"}:
        return "C" if up == "CERTO" else "E"

    return txt


def merge_questoes_com_gabarito(
    questoes_payload: Dict[str, Any],
    gabarito_payload: Dict[str, Any],
) -> Dict[str, Any]:
    merged = _normalize_payload_keys(questoes_payload)
    questoes = merged.get("questoes")

    if not isinstance(questoes, list):
        raise ValueError("Payload de prova inválido: 'questoes' não é lista.")

    gabarito_oficial = gabarito_payload.get("gabarito_oficial", {})
    if not isinstance(gabarito_oficial, dict):
        raise ValueError(
            "Payload de gabarito inválido: 'gabarito_oficial' não é objeto."
        )

    gabarito_map: Dict[int, str] = {}
    for k, v in gabarito_oficial.items():
        n = _parse_question_number(k)
        gv = _normalize_gabarito_value(v)
        if n is not None and gv is not None:
            gabarito_map[n] = gv

    for q in questoes:
        if not isinstance(q, dict):
            continue
        n = _parse_question_number(q.get("numero"))
        if n is None:
            continue
        if n in gabarito_map:
            q["gabarito"] = gabarito_map[n]
        else:
            # Mantém campo presente para respeitar export/shape
            q["gabarito"] = q.get("gabarito", "anulada")

    return merged


def _build_first_page_pdf_from_prova(prova_pdf_path: str, temp_dir: str) -> str:
    import pdfplumber

    prova_path = Path(prova_pdf_path)
    if not prova_path.exists():
        raise FileNotFoundError(
            f"Prova não encontrada para gerar capa: {prova_pdf_path}"
        )

    out_dir = Path(temp_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    first_page_pdf = out_dir / "prova_capa.pdf"

    with pdfplumber.open(str(prova_path)) as pdf:
        if not pdf.pages:
            raise ValueError(f"PDF da prova sem páginas: {prova_pdf_path}")

        first_page = pdf.pages[0]
        cropped = first_page.crop(
            (0, 0, float(first_page.width), float(first_page.height))
        )
        single_page_pdf = cropped.to_image(resolution=150).original

    single_page_pdf.save(first_page_pdf, "PDF")
    return str(first_page_pdf.resolve())


async def _process_row_pipeline(
    row: Dict[str, Any],
    row_state: Dict[str, Any],
    schema_like: Dict[str, Any],
    db_path: str,
    rate_limiter: SlidingWindowRateLimiter,
    success_logger: logging.Logger,
    error_logger: logging.Logger,
) -> None:
    row_id = row.get("id")
    if row_id is None:
        return

    # ETAPA 1: validação + extração de imagens da prova (pulando primeira página, como regra do extractor)
    try:
        print(f"[PIPE][id={row_id}] ETAPA 1: validando caminhos e extraindo imagens.")
        prova_path = ensure_local_absolute(str(row.get("prova_path", "")), "prova_path")
        gabarito_path = ensure_local_absolute(
            str(row.get("gabarito_path", "")), "gabarito_path"
        )
        row_state["prova_path"] = prova_path
        row_state["gabarito_path"] = gabarito_path

        manifest = await asyncio.to_thread(
            extract_images_from_prova,
            prova_pdf_path=prova_path,
            row_id=row_id,
            banca=str(row.get("banca", "")),
            instituicao=str(row.get("instituicao", "")),
            base_temp_dir=TEMP_BASE_DIR,
            logger=success_logger,
        )
        manifest_dict = manifest_to_dict(manifest)
        image_paths = [
            item["absolute_path"]
            for item in manifest_dict.get("images", [])
            if item.get("absolute_path")
        ]

        row_state["manifest"] = manifest
        row_state["manifest_dict"] = manifest_dict
        row_state["image_paths"] = image_paths
        row_state["temp_dir"] = manifest.output_dir

        print(
            f"[PIPE][id={row_id}] ETAPA 1 OK. Imagens extraídas da prova: {manifest.total_images}."
        )
    except Exception as exc:
        row_state["status"] = "pendente"
        row_state["reason"] = f"Falha na extração de imagens: {exc}"
        print(f"[PIPE][id={row_id}] ETAPA 1 ERRO: {exc}")
        error_logger.error(
            "Falha na etapa de extração de imagens id=%s: %s\n%s",
            row_id,
            exc,
            traceback.format_exc(),
        )
        return

    # ETAPA 2A: IA da prova (prova inteira + imagens extraídas + prompt_prova)
    try:
        print(f"[PIPE][id={row_id}] ETAPA 2A: IA PROVA.")
        ai_result_prova = await asyncio.to_thread(
            call_ai_prova_with_retries,
            prova_pdf_path=row_state["prova_path"],
            extracted_image_paths=row_state["image_paths"],
            prompt_path=PROMPT_PROVA_PATH,
            schema_path=JSON_MODEL_PATH,
            network_retries=3,
            invalid_json_extra_retry=1,
            backoff_base_seconds=2.0,
            rate_limiter=rate_limiter,
            logger=success_logger,
        )
        row_state["ai_result_prova"] = ai_result_prova

        if not ai_result_prova.success or not ai_result_prova.content:
            row_state["status"] = "pendente"
            row_state["reason"] = ai_result_prova.error or "IA prova sem sucesso"
            success_logger.info(
                "Registro id=%s mantido pendente. Motivo IA prova: %s",
                row_id,
                row_state["reason"],
            )
            return

        print(f"[PIPE][id={row_id}] ETAPA 2A OK.")
    except Exception as exc:
        row_state["status"] = "pendente"
        row_state["reason"] = f"Falha na IA (prova): {exc}"
        print(f"[PIPE][id={row_id}] ETAPA 2A ERRO: {exc}")
        error_logger.error(
            "Falha na etapa de IA PROVA id=%s: %s\n%s",
            row_id,
            exc,
            traceback.format_exc(),
        )
        return

    # ETAPA 2B: IA do gabarito (arquivo gabarito + primeira página da prova + prompt_gabarito)
    try:
        print(f"[PIPE][id={row_id}] ETAPA 2B: IA GABARITO.")
        temp_dir = row_state.get("temp_dir")
        if not temp_dir:
            raise ValueError("Diretório temporário ausente para gerar PDF da capa.")

        first_page_pdf = await asyncio.to_thread(
            _build_first_page_pdf_from_prova,
            row_state["prova_path"],
            temp_dir,
        )
        row_state["first_page_pdf_path"] = first_page_pdf

        ai_result_gabarito = await asyncio.to_thread(
            call_ai_gabarito_with_retries,
            gabarito_pdf_path=row_state["gabarito_path"],
            prova_capa_image_path=first_page_pdf,
            prompt_path=PROMPT_GABARITO_PATH,
            schema_path=JSON_MODEL_PATH,
            network_retries=3,
            invalid_json_extra_retry=1,
            backoff_base_seconds=2.0,
            rate_limiter=rate_limiter,
            logger=success_logger,
        )
        row_state["ai_result_gabarito"] = ai_result_gabarito

        if not ai_result_gabarito.success or not ai_result_gabarito.content:
            row_state["status"] = "pendente"
            row_state["reason"] = ai_result_gabarito.error or "IA gabarito sem sucesso"
            success_logger.info(
                "Registro id=%s mantido pendente. Motivo IA gabarito: %s",
                row_id,
                row_state["reason"],
            )
            return

        print(f"[PIPE][id={row_id}] ETAPA 2B OK.")
    except Exception as exc:
        row_state["status"] = "pendente"
        row_state["reason"] = f"Falha na IA (gabarito): {exc}"
        print(f"[PIPE][id={row_id}] ETAPA 2B ERRO: {exc}")
        error_logger.error(
            "Falha na etapa de IA GABARITO id=%s: %s\n%s",
            row_id,
            exc,
            traceback.format_exc(),
        )
        return

    # ETAPA 2C: merge prova + gabarito por número de questão
    try:
        print(f"[PIPE][id={row_id}] ETAPA 2C: merge questões + gabarito.")
        prova_payload = row_state["ai_result_prova"].content
        gabarito_payload = row_state["ai_result_gabarito"].content
        if prova_payload is None or gabarito_payload is None:
            row_state["status"] = "pendente"
            row_state["reason"] = "Payload ausente para merge"
            return

        merged_payload = merge_questoes_com_gabarito(
            questoes_payload=prova_payload,
            gabarito_payload=gabarito_payload,
        )
        row_state["merged_payload"] = merged_payload
        print(f"[PIPE][id={row_id}] ETAPA 2C OK.")
    except Exception as exc:
        row_state["status"] = "pendente"
        row_state["reason"] = f"Falha no merge: {exc}"
        print(f"[PIPE][id={row_id}] ETAPA 2C ERRO: {exc}")
        error_logger.error(
            "Falha na etapa de MERGE id=%s: %s\n%s",
            row_id,
            exc,
            traceback.format_exc(),
        )
        return

    # ETAPA 3: exportar
    try:
        print(f"[PIPE][id={row_id}] ETAPA 3: exportando JSON/imagens.")
        merged_payload = row_state["merged_payload"]
        if merged_payload is None:
            row_state["status"] = "pendente"
            row_state["reason"] = "Payload final ausente para exportação"
            return

        json_abs_path, images_abs_dir, copied_files = await asyncio.to_thread(
            persist_json_and_images,
            row=row_state["row"],
            payload=merged_payload,
            schema_like=schema_like,
            output_base_dir=OUTPUT_BASE_DIR,
            temp_images_dir=row_state["temp_dir"],
        )

        await asyncio.to_thread(
            update_row_to_extracted,
            row_id,
            str(Path(json_abs_path).resolve()),
            db_path,
        )

        row_state["json_abs_path"] = json_abs_path
        row_state["images_abs_dir"] = images_abs_dir
        row_state["copied_files"] = copied_files
        row_state["status"] = "extraido"
        row_state["reason"] = None

        manifest_total = (
            row_state["manifest"].total_images if row_state["manifest"] else 0
        )
        success_logger.info(
            "Extração concluída id=%s | json=%s | imagens_copiadas=%s | temp_imagens=%s",
            row_id,
            json_abs_path,
            len(copied_files),
            manifest_total,
        )
        print(
            f"[PIPE][id={row_id}] ETAPA 3 OK. JSON: {json_abs_path} | Imagens copiadas: {len(copied_files)}"
        )
    except Exception as exc:
        row_state["status"] = "pendente"
        row_state["reason"] = f"Falha na exportação: {exc}"
        print(f"[PIPE][id={row_id}] ETAPA 3 ERRO: {exc}")
        error_logger.error(
            "Falha na etapa de exportação id=%s: %s\n%s",
            row_id,
            exc,
            traceback.format_exc(),
        )
    finally:
        print(f"[PIPE][id={row_id}] ETAPA 4: aguardando limpeza ao final do ciclo.")


def run_extraction_pipeline(db_path: str = DB_PATH) -> Dict[str, Any]:
    print(
        "[PIPELINE] Inicializando pipeline de extração em lotes concorrentes por item."
    )
    success_logger, error_logger = setup_loggers()
    print("[PIPELINE] Loggers configurados com sucesso.")

    print(f"[PIPELINE] Carregando modelo JSON de: {JSON_MODEL_PATH}")
    schema_like = load_schema_like(JSON_MODEL_PATH)
    print("[PIPELINE] Modelo JSON carregado.")

    print(f"[PIPELINE] Buscando registros pendentes em '{TABLE_NAME}'.")
    pending_rows = fetch_pending_rows(db_path=db_path)
    total = len(pending_rows)
    print(f"[PIPELINE] Total de registros pendentes encontrados: {total}")

    if total == 0:
        print("[PIPELINE] Nenhum registro pendente para processar.")
        success_logger.info("Nenhum registro pendente encontrado.")
        return {"total": 0, "extraidos": 0, "pendentes": 0, "detalhes": []}

    print(
        f"[PIPELINE] Configurando rate limit IA: {AI_MAX_PER_MINUTE}/min e {AI_MAX_PER_DAY}/dia."
    )
    rate_limiter = SlidingWindowRateLimiter(
        max_per_minute=AI_MAX_PER_MINUTE,
        max_per_day=AI_MAX_PER_DAY,
    )

    batches = chunked(pending_rows, BATCH_SIZE)
    print(
        f"[PIPELINE] Total de ciclos (lotes): {len(batches)} | Tamanho do lote: {BATCH_SIZE}"
    )

    details: List[Dict[str, Any]] = []
    extraidos = 0
    pendentes = 0

    for batch_idx, batch_rows in enumerate(batches, start=1):
        print(f"\n[PIPELINE] ===== Início do ciclo {batch_idx}/{len(batches)} =====")
        print(
            f"[PIPELINE] Registros no ciclo: {len(batch_rows)} (concorrentes, fluxo encadeado por item)"
        )

        state = _build_batch_state(batch_rows)

        async def _run_batch() -> None:
            tasks = []
            for row in batch_rows:
                row_id = row.get("id")
                if row_id is None:
                    continue
                tasks.append(
                    asyncio.create_task(
                        _process_row_pipeline(
                            row=row,
                            row_state=state[row_id],
                            schema_like=schema_like,
                            db_path=db_path,
                            rate_limiter=rate_limiter,
                            success_logger=success_logger,
                            error_logger=error_logger,
                        )
                    )
                )
            if tasks:
                await asyncio.gather(*tasks)

        asyncio.run(_run_batch())

        print(f"[ETAPA 4][CICLO {batch_idx}] Limpando temporários do lote...")
        for row in batch_rows:
            row_id = row.get("id")
            if row_id is None:
                continue
            row_state = state[row_id]
            cleanup_temp_dir(
                temp_dir=row_state.get("temp_dir"),
                error_logger=error_logger,
                row_id=row_id,
            )

        print(f"[PIPELINE] Consolidando resultados do ciclo {batch_idx}...")
        for row in batch_rows:
            row_id = row.get("id")
            if row_id is None:
                continue
            row_state = state[row_id]
            status = row_state["status"]

            if status == "extraido":
                extraidos += 1
                details.append(
                    {
                        "id": row_id,
                        "status": "extraido",
                        "json_path": row_state["json_abs_path"],
                        "images_dir": row_state["images_abs_dir"],
                        "copied_images": len(row_state["copied_files"]),
                    }
                )
                print(f"[PIPELINE][id={row_id}] Final do ciclo: EXTRAIDO")
            else:
                pendentes += 1
                details.append(
                    {
                        "id": row_id,
                        "status": "pendente",
                        "reason": row_state["reason"] or "Falha não especificada",
                    }
                )
                print(
                    f"[PIPELINE][id={row_id}] Final do ciclo: PENDENTE | Motivo: {row_state['reason']}"
                )

        print(f"[PIPELINE] ===== Fim do ciclo {batch_idx}/{len(batches)} =====")

    summary = {
        "total": total,
        "extraidos": extraidos,
        "pendentes": pendentes,
        "detalhes": details,
    }

    print(
        f"[PIPELINE] Resumo final => total={total}, extraidos={extraidos}, pendentes={pendentes}"
    )
    success_logger.info(
        "Resumo execução | total=%s | extraidos=%s | pendentes=%s",
        total,
        extraidos,
        pendentes,
    )
    return summary


if __name__ == "__main__":
    print("[MAIN] Execução iniciada.")
    result = run_extraction_pipeline(DB_PATH)
    print("[MAIN] Execução finalizada. Resumo:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
