from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pdfplumber
from PIL import Image


@dataclass
class ExtractedImage:
    file_name: str
    absolute_path: str
    page_number: int
    index_on_page: int
    source: str  # "embedded" | "rendered-crop"
    bbox: Optional[List[float]] = None


@dataclass
class ExtractionManifest:
    output_dir: str
    total_images: int
    images: List[ExtractedImage]
    per_page_count: Dict[int, int]


def _sanitize_fragment(value: str) -> str:
    value = value.strip()
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"\s+", "-", value)
    value = value.strip(" .")
    return value or "na"


def build_temp_dir(base_temp_dir: str, row_id: int | str, banca: str, instituicao: str) -> Path:
    prefix = f"{row_id}-{_sanitize_fragment(str(banca))}-{_sanitize_fragment(str(instituicao))}"
    out_dir = Path(base_temp_dir) / "provas" / prefix
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _save_pil_as_jpeg(img: Image.Image, out_path: Path, quality: int = 90) -> None:
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    img.save(out_path, format="JPEG", quality=quality, optimize=True)


def _extract_bbox(img_obj: Dict[str, Any]) -> Optional[List[float]]:
    try:
        x0_raw = img_obj.get("x0")
        top_raw = img_obj.get("top")
        x1_raw = img_obj.get("x1")
        bottom_raw = img_obj.get("bottom")

        if x0_raw is None or top_raw is None or x1_raw is None or bottom_raw is None:
            return None

        x0 = float(x0_raw)
        top = float(top_raw)
        x1 = float(x1_raw)
        bottom = float(bottom_raw)

        if x1 <= x0 or bottom <= top:
            return None

        return [x0, top, x1, bottom]
    except Exception:
        return None


def _bbox_key(bbox: Optional[List[float]]) -> Optional[Tuple[int, int, int, int]]:
    if not bbox:
        return None
    # arredonda para deduplicação robusta
    return (round(bbox[0]), round(bbox[1]), round(bbox[2]), round(bbox[3]))


def _render_page_rgb(page: Any, dpi: int = 200) -> Image.Image:
    rendered = page.to_image(resolution=dpi).original
    if rendered.mode != "RGB":
        rendered = rendered.convert("RGB")
    return rendered


def _crop_from_bbox(page_image: Image.Image, page: Any, bbox: List[float]) -> Optional[Image.Image]:
    try:
        width_px, height_px = page_image.size
        page_w = float(page.width)
        page_h = float(page.height)

        scale_x = width_px / page_w if page_w else 1.0
        scale_y = height_px / page_h if page_h else 1.0

        x0, top, x1, bottom = bbox

        left_px = max(0, int(x0 * scale_x))
        top_px = max(0, int(top * scale_y))
        right_px = min(width_px, int(x1 * scale_x))
        bottom_px = min(height_px, int(bottom * scale_y))

        if right_px <= left_px or bottom_px <= top_px:
            return None

        crop = page_image.crop((left_px, top_px, right_px, bottom_px))
        return crop
    except Exception:
        return None


def _extract_images_from_page(
    page: Any,
    page_number: int,
    out_dir: Path,
    per_page_counter: Dict[int, int],
    logger: logging.Logger,
    render_dpi: int = 200,
) -> List[ExtractedImage]:
    extracted: List[ExtractedImage] = []
    page_images = page.images or []
    if not page_images:
        return extracted

    seen_bboxes: Set[Tuple[int, int, int, int]] = set()
    rendered_page: Optional[Image.Image] = None

    for img_obj in page_images:
        bbox = _extract_bbox(img_obj)
        bkey = _bbox_key(bbox)

        # 1) Tentativa principal: imagem embutida
        embedded_ok = False
        try:
            image_dict = None
            if img_obj.get("name") is not None:
                image_dict = page.extract_image(img_obj["name"])
            elif img_obj.get("object_id") is not None:
                image_dict = page.extract_image(img_obj["object_id"])

            if image_dict and "image" in image_dict:
                raw_bytes = image_dict["image"]
                with Image.open(io.BytesIO(raw_bytes)) as pil_img:
                    per_page_counter[page_number] = per_page_counter.get(page_number, 0) + 1
                    idx = per_page_counter[page_number]
                    file_name = f"img_pg{page_number}_{idx}.jpeg"
                    file_path = out_dir / file_name
                    _save_pil_as_jpeg(pil_img, file_path)

                    extracted.append(
                        ExtractedImage(
                            file_name=file_name,
                            absolute_path=str(file_path.resolve()),
                            page_number=page_number,
                            index_on_page=idx,
                            source="embedded",
                            bbox=bbox,
                        )
                    )
                    embedded_ok = True
                    if bkey is not None:
                        seen_bboxes.add(bkey)
        except Exception as exc:
            logger.warning(
                "Falha em extract_image na página %s (obj=%s): %s",
                page_number,
                img_obj.get("name") or img_obj.get("object_id"),
                exc,
            )

        # 2) Fallback: crop por bbox (somente área da figura)
        if not embedded_ok and bbox is not None:
            if bkey is not None and bkey in seen_bboxes:
                continue

            try:
                if rendered_page is None:
                    rendered_page = _render_page_rgb(page, dpi=render_dpi)

                crop = _crop_from_bbox(rendered_page, page, bbox)
                if crop is None:
                    continue

                per_page_counter[page_number] = per_page_counter.get(page_number, 0) + 1
                idx = per_page_counter[page_number]
                file_name = f"img_pg{page_number}_{idx}.jpeg"
                file_path = out_dir / file_name
                _save_pil_as_jpeg(crop, file_path)

                extracted.append(
                    ExtractedImage(
                        file_name=file_name,
                        absolute_path=str(file_path.resolve()),
                        page_number=page_number,
                        index_on_page=idx,
                        source="rendered-crop",
                        bbox=bbox,
                    )
                )
                if bkey is not None:
                    seen_bboxes.add(bkey)
            except Exception as exc:
                logger.warning(
                    "Falha no crop por bbox na página %s: %s",
                    page_number,
                    exc,
                )

    return extracted


def extract_images_from_prova(
    prova_pdf_path: str,
    row_id: int | str,
    banca: str,
    instituicao: str,
    base_temp_dir: str = "temp",
    render_dpi: int = 200,
    logger: Optional[logging.Logger] = None,
) -> ExtractionManifest:
    """
    Extrai imagens da prova pulando SEMPRE a primeira página:
    - tenta imagens embutidas
    - fallback por crop da bbox de page.images (sem salvar página inteira)
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    prova_path = Path(prova_pdf_path)
    if not prova_path.exists():
        raise FileNotFoundError(f"Arquivo de prova não encontrado: {prova_pdf_path}")

    out_dir = build_temp_dir(base_temp_dir, row_id, banca, instituicao)

    all_images: List[ExtractedImage] = []
    per_page_count: Dict[int, int] = {}

    with pdfplumber.open(str(prova_path)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            if page_num == 1:
                continue

            page_extracted = _extract_images_from_page(
                page=page,
                page_number=page_num,
                out_dir=out_dir,
                per_page_counter=per_page_count,
                logger=logger,
                render_dpi=render_dpi,
            )
            all_images.extend(page_extracted)

    return ExtractionManifest(
        output_dir=str(out_dir.resolve()),
        total_images=len(all_images),
        images=all_images,
        per_page_count=per_page_count,
    )


def manifest_to_dict(manifest: ExtractionManifest) -> Dict[str, Any]:
    return {
        "output_dir": manifest.output_dir,
        "total_images": manifest.total_images,
        "per_page_count": manifest.per_page_count,
        "images": [
            {
                "file_name": img.file_name,
                "absolute_path": img.absolute_path,
                "page_number": img.page_number,
                "index_on_page": img.index_on_page,
                "source": img.source,
                "bbox": img.bbox,
            }
            for img in manifest.images
        ],
    }
