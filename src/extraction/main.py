from __future__ import annotations

import json
import logging
import sqlite3
import traceback
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
            "temp_dir": None,
            "ai_result": None,
            "json_abs_path": None,
            "images_abs_dir": None,
            "copied_files": [],
            "status": "pendente",
            "reason": None,
        }
    return state


def run_extraction_pipeline(db_path: str = DB_PATH) -> Dict[str, Any]:
    print("[PIPELINE] Inicializando pipeline de extração em lotes por etapa.")
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
        print(f"[PIPELINE] Registros no ciclo: {len(batch_rows)}")

        state = _build_batch_state(batch_rows)

        # ETAPA 1: EXTRAIR IMAGENS DE TODAS AS PROVAS DO LOTE
        print(
            f"[ETAPA 1][CICLO {batch_idx}] Extraindo imagens de {len(batch_rows)} provas..."
        )
        for row in batch_rows:
            row_id = row.get("id")
            if row_id is None:
                continue

            print(f"[ETAPA 1][id={row_id}] Iniciando validação de caminhos.")
            try:
                prova_path = ensure_local_absolute(
                    str(row.get("prova_path", "")), "prova_path"
                )
                gabarito_path = ensure_local_absolute(
                    str(row.get("gabarito_path", "")), "gabarito_path"
                )

                state[row_id]["prova_path"] = prova_path
                state[row_id]["gabarito_path"] = gabarito_path

                print(f"[ETAPA 1][id={row_id}] Extraindo imagens da prova.")
                manifest = extract_images_from_prova(
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

                state[row_id]["manifest"] = manifest
                state[row_id]["manifest_dict"] = manifest_dict
                state[row_id]["image_paths"] = image_paths
                state[row_id]["temp_dir"] = manifest.output_dir

                print(
                    f"[ETAPA 1][id={row_id}] OK. Imagens extraídas: {manifest.total_images} | "
                    f"Temp: {manifest.output_dir}"
                )
            except Exception as exc:
                state[row_id]["status"] = "pendente"
                state[row_id]["reason"] = f"Falha na extração de imagens: {exc}"
                print(f"[ETAPA 1][id={row_id}] ERRO: {exc}")
                error_logger.error(
                    "Falha na etapa de extração de imagens id=%s: %s\n%s",
                    row_id,
                    exc,
                    traceback.format_exc(),
                )

        # ETAPA 2: PROCESSAR IA PARA TODAS AS PROVAS VÁLIDAS DO LOTE
        print(f"[ETAPA 2][CICLO {batch_idx}] Processando IA para os registros aptos...")
        for row in batch_rows:
            row_id = row.get("id")
            if row_id is None:
                continue

            row_state = state[row_id]
            if row_state["prova_path"] is None or row_state["gabarito_path"] is None:
                print(f"[ETAPA 2][id={row_id}] Pulado (falha anterior).")
                continue

            print(
                f"[ETAPA 2][id={row_id}] Chamando IA com {len(row_state['image_paths'])} imagens."
            )
            try:
                ai_result = call_ai_with_retries(
                    prova_pdf_path=row_state["prova_path"],
                    gabarito_pdf_path=row_state["gabarito_path"],
                    extracted_image_paths=row_state["image_paths"],
                    prompt_path=PROMPT_PATH,
                    schema_path=JSON_MODEL_PATH,
                    network_retries=3,
                    invalid_json_extra_retry=1,
                    backoff_base_seconds=2.0,
                    rate_limiter=rate_limiter,
                    logger=success_logger,
                )
                row_state["ai_result"] = ai_result

                print(
                    f"[ETAPA 2][id={row_id}] Retorno IA: success={ai_result.success} "
                    f"attempts={ai_result.attempts}"
                )

                if not ai_result.success or not ai_result.content:
                    row_state["status"] = "pendente"
                    row_state["reason"] = ai_result.error or "IA sem sucesso"
                    print(
                        f"[ETAPA 2][id={row_id}] Conteúdo inválido/ausente. "
                        f"Permanece pendente. Motivo: {row_state['reason']}"
                    )
                    success_logger.info(
                        "Registro id=%s mantido como pendente. Motivo IA: %s",
                        row_id,
                        row_state["reason"],
                    )
            except Exception as exc:
                row_state["status"] = "pendente"
                row_state["reason"] = f"Falha na IA: {exc}"
                print(f"[ETAPA 2][id={row_id}] ERRO: {exc}")
                error_logger.error(
                    "Falha na etapa de IA id=%s: %s\n%s",
                    row_id,
                    exc,
                    traceback.format_exc(),
                )

        # ETAPA 3: EXPORTAR QUESTÕES DAS PROVAS VÁLIDAS DO LOTE
        print(
            f"[ETAPA 3][CICLO {batch_idx}] Exportando JSON/imagens dos registros aptos..."
        )
        for row in batch_rows:
            row_id = row.get("id")
            if row_id is None:
                continue

            row_state = state[row_id]
            ai_result = row_state["ai_result"]

            if not ai_result or not ai_result.success or not ai_result.content:
                print(f"[ETAPA 3][id={row_id}] Pulado (falha anterior).")
                continue

            try:
                print(
                    f"[ETAPA 3][id={row_id}] Persistindo JSON e imagens referenciadas."
                )
                json_abs_path, images_abs_dir, copied_files = persist_json_and_images(
                    row=row_state["row"],
                    payload=ai_result.content,
                    schema_like=schema_like,
                    output_base_dir=OUTPUT_BASE_DIR,
                    temp_images_dir=row_state["temp_dir"],
                )

                update_row_to_extracted(
                    row_id=row_id,
                    questoes_path=str(Path(json_abs_path).resolve()),
                    db_path=db_path,
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
                    f"[ETAPA 3][id={row_id}] OK. JSON: {json_abs_path} | "
                    f"Imagens copiadas: {len(copied_files)}"
                )

            except Exception as exc:
                row_state["status"] = "pendente"
                row_state["reason"] = f"Falha na exportação: {exc}"
                print(f"[ETAPA 3][id={row_id}] ERRO: {exc}")
                error_logger.error(
                    "Falha na etapa de exportação id=%s: %s\n%s",
                    row_id,
                    exc,
                    traceback.format_exc(),
                )

        # ETAPA 4: LIMPAR TEMP DE TODAS AS PROVAS DO LOTE
        print(f"[ETAPA 4][CICLO {batch_idx}] Limpando temporários do lote...")
        for row in batch_rows:
            row_id = row.get("id")
            if row_id is None:
                continue
            row_state = state[row_id]
            cleanup_temp_dir(
                temp_dir=row_state["temp_dir"],
                error_logger=error_logger,
                row_id=row_id,
            )

        # Consolidação de resultados do lote
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
                    f"[PIPELINE][id={row_id}] Final do ciclo: PENDENTE | "
                    f"Motivo: {row_state['reason']}"
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
