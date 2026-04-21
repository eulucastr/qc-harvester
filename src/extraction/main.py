from __future__ import annotations

import json
import logging
import sqlite3
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import rmtree
from typing import Any, Dict, List, Optional, Tuple

from .export import persist_json_and_images
from .extract_images import extract_images_from_prova, manifest_to_dict
from .use_ai import SlidingWindowRateLimiter, call_ai_with_retries

DB_PATH = "mentoria.db"
TABLE_NAME = "concursos"

PROMPT_PATH = "config/prompt.md"
JSON_MODEL_PATH = "config/json_model.json"

TEMP_BASE_DIR = "temp"
OUTPUT_BASE_DIR = "D:/mentor.ia/questoes"

SUCCESS_LOG_PATH = "output/extraction.sucess.log"
ERROR_LOG_PATH = "output/extraction.error.log"

MAX_WORKERS = 5
AI_MAX_PER_MINUTE = 10
AI_MAX_PER_DAY = 500


def setup_loggers() -> Tuple[logging.Logger, logging.Logger]:
    Path("output").mkdir(parents=True, exist_ok=True)

    success_logger = logging.getLogger("extraction_success")
    error_logger = logging.getLogger("extraction_error")

    success_logger.setLevel(logging.INFO)
    error_logger.setLevel(logging.ERROR)

    # evita handlers duplicados quando o script é importado/rodado mais de uma vez
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
    """
    with get_db_connection(db_path) as conn:
        rows = conn.execute(query).fetchall()
    return [dict(row) for row in rows]


def update_row_to_extracted(
    row_id: Any,
    questoes_path: str,
    db_path: str = DB_PATH,
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


def cleanup_temp_dir(temp_dir: Optional[str], error_logger: logging.Logger) -> None:
    if not temp_dir:
        return
    try:
        temp_path = Path(temp_dir)
        if temp_path.exists():
            rmtree(temp_path, ignore_errors=True)
    except Exception as exc:
        error_logger.error("Falha ao limpar pasta temporária '%s': %s", temp_dir, exc)


def process_single_row(
    row: Dict[str, Any],
    schema_like: Dict[str, Any],
    rate_limiter: SlidingWindowRateLimiter,
    success_logger: logging.Logger,
    error_logger: logging.Logger,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    row_id = row.get("id")
    if row_id is None:
        raise ValueError("Registro sem 'id' não pode ser processado.")
    temp_dir = None

    try:
        prova_path = ensure_local_absolute(str(row.get("prova_path", "")), "prova_path")
        gabarito_path = ensure_local_absolute(
            str(row.get("gabarito_path", "")), "gabarito_path"
        )

        manifest = extract_images_from_prova(
            prova_pdf_path=prova_path,
            row_id=row_id,
            banca=str(row.get("banca", "")),
            instituicao=str(row.get("instituicao", "")),
            base_temp_dir=TEMP_BASE_DIR,
            logger=success_logger,
        )
        temp_dir = manifest.output_dir
        manifest_dict = manifest_to_dict(manifest)

        image_paths = [
            item["absolute_path"]
            for item in manifest_dict.get("images", [])
            if item.get("absolute_path")
        ]

        ai_result = call_ai_with_retries(
            prova_pdf_path=prova_path,
            gabarito_pdf_path=gabarito_path,
            extracted_image_paths=image_paths,
            prompt_path=PROMPT_PATH,
            schema_path=JSON_MODEL_PATH,
            network_retries=3,
            invalid_json_extra_retry=1,
            backoff_base_seconds=2.0,
            rate_limiter=rate_limiter,
            logger=success_logger,
        )

        # Regra: se falhar ou JSON inválido após 1 retry extra, mantém pendente
        if not ai_result.success or not ai_result.content:
            success_logger.info(
                "Registro id=%s mantido como pendente. Motivo IA: %s",
                row_id,
                ai_result.error or "resultado sem conteúdo",
            )
            return {
                "id": row_id,
                "status": "pendente",
                "reason": ai_result.error or "IA sem sucesso",
            }

        json_abs_path, images_abs_dir, copied_files = persist_json_and_images(
            row=row,
            payload=ai_result.content,
            schema_like=schema_like,
            output_base_dir=OUTPUT_BASE_DIR,
            temp_images_dir=temp_dir,
        )

        update_row_to_extracted(
            row_id=row_id,
            questoes_path=str(Path(json_abs_path).resolve()),
            db_path=db_path,
        )

        success_logger.info(
            "Extração concluída id=%s | json=%s | imagens_copiadas=%s | temp_imagens=%s",
            row_id,
            json_abs_path,
            len(copied_files),
            manifest.total_images,
        )

        return {
            "id": row_id,
            "status": "extraido",
            "json_path": json_abs_path,
            "images_dir": images_abs_dir,
            "copied_images": len(copied_files),
        }

    except Exception as exc:
        # Sem status "erro": mantém pendente e registra log de erro
        error_logger.error(
            "Falha ao processar id=%s: %s\n%s",
            row_id,
            exc,
            traceback.format_exc(),
        )
        return {"id": row_id, "status": "pendente", "reason": str(exc)}
    finally:
        cleanup_temp_dir(temp_dir, error_logger=error_logger)


def run_extraction_pipeline(db_path: str = DB_PATH) -> Dict[str, Any]:
    success_logger, error_logger = setup_loggers()
    schema_like = load_schema_like(JSON_MODEL_PATH)

    pending_rows = fetch_pending_rows(db_path=db_path)
    total = len(pending_rows)

    if total == 0:
        success_logger.info("Nenhum registro pendente encontrado.")
        return {"total": 0, "extraidos": 0, "pendentes": 0, "detalhes": []}

    rate_limiter = SlidingWindowRateLimiter(
        max_per_minute=AI_MAX_PER_MINUTE,
        max_per_day=AI_MAX_PER_DAY,
    )

    details: List[Dict[str, Any]] = []
    extraidos = 0
    pendentes = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(
                process_single_row,
                row,
                schema_like,
                rate_limiter,
                success_logger,
                error_logger,
                db_path,
            )
            for row in pending_rows
        ]

        for future in as_completed(futures):
            result = future.result()
            details.append(result)
            if result.get("status") == "extraido":
                extraidos += 1
            else:
                pendentes += 1

    summary = {
        "total": total,
        "extraidos": extraidos,
        "pendentes": pendentes,
        "detalhes": details,
    }

    success_logger.info(
        "Resumo execução | total=%s | extraidos=%s | pendentes=%s",
        total,
        extraidos,
        pendentes,
    )

    return summary


if __name__ == "__main__":
    result = run_extraction_pipeline(DB_PATH)
    print(json.dumps(result, ensure_ascii=False, indent=2))
