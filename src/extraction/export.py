from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple


class ExportValidationError(ValueError):
    """Erro de validação do payload antes da exportação."""


def _sanitize_fragment(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"\s+", "-", value)
    value = value.strip(" .")
    return value


def _normalize_optional_fragment(value: Any) -> str:
    if value is None:
        return ""
    txt = str(value).strip()
    if txt.lower() in {"", "none", "null", "na", "n/a"}:
        return ""
    return _sanitize_fragment(txt)


def _build_base_name(row: Dict[str, Any]) -> str:
    row_id = _normalize_optional_fragment(row.get("id"))
    banca = _normalize_optional_fragment(row.get("banca"))
    instituicao = _normalize_optional_fragment(row.get("instituicao"))
    cargo = _normalize_optional_fragment(row.get("cargo"))
    especialidade = _normalize_optional_fragment(row.get("especialidade"))
    ano = _normalize_optional_fragment(row.get("ano"))

    parts = [row_id, banca, instituicao, cargo]
    if especialidade:
        parts.append(especialidade)
    parts.append(ano)

    parts = [p for p in parts if p]
    return "-".join(parts)


def _normalize_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    if "questões" in data and "questoes" not in data:
        data["questoes"] = data.pop("questões")
    return data


def validate_payload_shape(
    payload: Dict[str, Any], schema_like: Dict[str, Any] | None = None
) -> None:
    data = _normalize_keys(payload)

    if not isinstance(data, dict):
        raise ExportValidationError("Payload deve ser um objeto JSON.")

    if "questoes" not in data:
        raise ExportValidationError(
            "Chave obrigatória ausente: 'questoes' (ou 'questões')."
        )
    if "discursivas" not in data:
        raise ExportValidationError("Chave obrigatória ausente: 'discursivas'.")

    if not isinstance(data["questoes"], list):
        raise ExportValidationError("'questoes' deve ser uma lista.")
    if not isinstance(data["discursivas"], list):
        raise ExportValidationError("'discursivas' deve ser uma lista.")

    for i, q in enumerate(data["questoes"], start=1):
        if not isinstance(q, dict):
            raise ExportValidationError(f"questoes[{i}] deve ser um objeto.")
        for key in ("numero", "enunciado", "gabarito"):
            if key not in q:
                raise ExportValidationError(
                    f"questoes[{i}] sem chave obrigatória '{key}'."
                )

    for i, d in enumerate(data["discursivas"], start=1):
        if not isinstance(d, dict):
            raise ExportValidationError(f"discursivas[{i}] deve ser um objeto.")
        if "numero" not in d or "enunciado" not in d:
            raise ExportValidationError(
                f"discursivas[{i}] sem chaves obrigatórias 'numero' e/ou 'enunciado'."
            )

    _ = schema_like


def _collect_strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
        return
    if isinstance(node, dict):
        for v in node.values():
            yield from _collect_strings(v)
        return
    if isinstance(node, list):
        for item in node:
            yield from _collect_strings(item)
        return


def extract_referenced_images(payload: Dict[str, Any]) -> Set[str]:
    tag_pattern = re.compile(
        r"\[\[([^\[\]]+\.(?:jpe?g|png|webp|gif|bmp|tiff?))\]\]", re.IGNORECASE
    )
    referenced: Set[str] = set()

    for text in _collect_strings(payload):
        for match in tag_pattern.findall(text):
            referenced.add(Path(match).name)

    return referenced


def _index_temp_images(temp_images_dir: str) -> Dict[str, Path]:
    root = Path(temp_images_dir)
    if not root.exists():
        return {}

    indexed: Dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
            ".tif",
            ".tiff",
        }:
            indexed[p.name] = p
    return indexed


def persist_json_and_images(
    row: Dict[str, Any],
    payload: Dict[str, Any],
    schema_like: Dict[str, Any] | None = None,
    output_base_dir: str = "D:/mentor.ia/questoes",
    temp_images_dir: str | None = None,
) -> Tuple[str, str, List[str]]:
    normalized = _normalize_keys(payload)
    validate_payload_shape(normalized, schema_like=schema_like)

    base_name = _build_base_name(row)

    json_dir = Path(output_base_dir)
    json_dir.mkdir(parents=True, exist_ok=True)

    json_path = (json_dir / f"{base_name}.json").resolve()

    referenced = extract_referenced_images(normalized)
    copied_files: List[str] = []
    images_dir_resolved = ""

    if temp_images_dir and referenced:
        indexed = _index_temp_images(temp_images_dir)
        to_copy: List[Tuple[Path, str]] = []

        for file_name in sorted(referenced):
            src = indexed.get(file_name)
            if src:
                to_copy.append((src, file_name))

        # Só cria a pasta se realmente houver algo para copiar
        if to_copy:
            images_dir = json_dir / "imagens" / base_name
            images_dir.mkdir(parents=True, exist_ok=True)

            for src, file_name in to_copy:
                dst = images_dir / file_name
                shutil.copy2(src, dst)
                copied_files.append(str(dst.resolve()))

            images_dir_resolved = str(images_dir.resolve())

    json_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return str(json_path), images_dir_resolved, copied_files
