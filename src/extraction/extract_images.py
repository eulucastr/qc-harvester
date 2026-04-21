from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pdfplumber
from PIL import Image


@dataclass
class ExtractedImage:
    file_name: str
    absolute_path: str
    page_number: int
    index_on_page: int
    source: str  # "embedded" | "rendered-crop" | "rendered-page"
    bbox: Optional[List[float]] = None


@dataclass
class ExtractionManifest:
    output_dir: str
    total_images: int
    images: List[ExtractedImage]
    per_page_count: Dict[int, int]


def _sanitize_fragment(value: str) -> str:
    """
    Sanitiza fragmentos para uso em paths no Windows.
    """
    value = value.strip()
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"\s+", "-", value)
    value = value.strip(" .")
    return value or "na"


def build_temp_dir(
    base_temp_dir: str, row_id: int | str, banca: str, instituicao: str
) -> Path:
    """
    Cria path no padrão: temp/provas/[id]-[banca]-[instituicao]
    """
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


def _try_extract_embedded_images(
    page: Any,
    page_number: int,
    out_dir: Path,
    per_page_counter: Dict[int, int],
    logger: logging.Logger,
) -> List[ExtractedImage]:
    """
    Extrai imagens embutidas (raster) usando pdfplumber.
    """
    extracted: List[ExtractedImage] = []
    images = page.images or []

    for img_obj in images:
        try:
            # page.extract_image aceita o "name" do objeto quando disponível
            # Em alguns PDFs, apenas "object_id" está presente.
            image_dict = None
            if "name" in img_obj and img_obj["name"] is not None:
                image_dict = page.extract_image(img_obj["name"])
            elif "object_id" in img_obj and img_obj["object_id"] is not None:
                image_dict = page.extract_image(img_obj["object_id"])

            if not image_dict or "image" not in image_dict:
                continue

            raw_bytes = image_dict["image"]
            with Image.open(io.BytesIO(raw_bytes)) as pil_img:
                per_page_counter[page_number] = per_page_counter.get(page_number, 0) + 1
                idx = per_page_counter[page_number]
                file_name = f"img_pg{page_number}_{idx}.jpeg"
                file_path = out_dir / file_name
                _save_pil_as_jpeg(pil_img, file_path)

                bbox = None
                try:
                    x0_raw = img_obj.get("x0")
                    top_raw = img_obj.get("top")
                    x1_raw = img_obj.get("x1")
                    bottom_raw = img_obj.get("bottom")

                    if (
                        x0_raw is not None
                        and top_raw is not None
                        and x1_raw is not None
                        and bottom_raw is not None
                    ):
                        x0_f: float = float(x0_raw)
                        top_f: float = float(top_raw)
                        x1_f: float = float(x1_raw)
                        bottom_f: float = float(bottom_raw)
                        bbox = [x0_f, top_f, x1_f, bottom_f]
                except Exception:
                    bbox = None

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
        except Exception as exc:
            logger.warning(
                "Falha ao extrair imagem embutida da página %s: %s",
                page_number,
                exc,
            )

    return extracted


def _render_page(page: Any, dpi: int = 180) -> Image.Image:
    """
    Renderiza página inteira para capturar conteúdo vetorial/tabelas.
    """
    page_img = page.to_image(resolution=dpi).original
    if page_img.mode != "RGB":
        page_img = page_img.convert("RGB")
    return page_img


def _extract_rendered_crops(
    page: Any,
    page_number: int,
    out_dir: Path,
    per_page_counter: Dict[int, int],
    logger: logging.Logger,
    dpi: int = 180,
) -> List[ExtractedImage]:
    """
    Para cada bounding box de imagem detectada no PDF, renderiza o recorte
    da página para capturar também objetos vetoriais associados.
    """
    extracted: List[ExtractedImage] = []
    if not page.images:
        return extracted

    rendered_page = _render_page(page, dpi=dpi)
    width_px, height_px = rendered_page.size
    page_w = float(page.width)
    page_h = float(page.height)

    scale_x = width_px / page_w if page_w else 1.0
    scale_y = height_px / page_h if page_h else 1.0

    for img_obj in page.images:
        try:
            x0 = float(img_obj.get("x0", 0.0))
            top = float(img_obj.get("top", 0.0))
            x1 = float(img_obj.get("x1", 0.0))
            bottom = float(img_obj.get("bottom", 0.0))

            left_px = max(0, int(x0 * scale_x))
            top_px = max(0, int(top * scale_y))
            right_px = min(width_px, int(x1 * scale_x))
            bottom_px = min(height_px, int(bottom * scale_y))

            if right_px <= left_px or bottom_px <= top_px:
                continue

            crop = rendered_page.crop((left_px, top_px, right_px, bottom_px))
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
                    bbox=[x0, top, x1, bottom],
                )
            )
        except Exception as exc:
            logger.warning(
                "Falha ao renderizar recorte vetorial na página %s: %s",
                page_number,
                exc,
            )

    return extracted


def _fallback_full_page_render(
    page: Any,
    page_number: int,
    out_dir: Path,
    per_page_counter: Dict[int, int],
    logger: logging.Logger,
    dpi: int = 180,
) -> Optional[ExtractedImage]:
    """
    Fallback: se nada foi extraído da página, salva render da página inteira
    para garantir cobertura de figuras/tabelas vetoriais.
    """
    try:
        rendered_page = _render_page(page, dpi=dpi)
        per_page_counter[page_number] = per_page_counter.get(page_number, 0) + 1
        idx = per_page_counter[page_number]
        file_name = f"img_pg{page_number}_{idx}.jpeg"
        file_path = out_dir / file_name
        _save_pil_as_jpeg(rendered_page, file_path)

        return ExtractedImage(
            file_name=file_name,
            absolute_path=str(file_path.resolve()),
            page_number=page_number,
            index_on_page=idx,
            source="rendered-page",
            bbox=[0.0, 0.0, float(page.width), float(page.height)],
        )
    except Exception as exc:
        logger.warning("Falha no fallback de página inteira %s: %s", page_number, exc)
        return None


def extract_images_from_prova(
    prova_pdf_path: str,
    row_id: int | str,
    banca: str,
    instituicao: str,
    base_temp_dir: str = "temp",
    render_dpi: int = 180,
    logger: Optional[logging.Logger] = None,
) -> ExtractionManifest:
    """
    Extrai imagens da prova (sempre pulando a primeira página), incluindo:
    1) Imagens raster embutidas
    2) Recortes renderizados para capturar vetores/tabelas
    3) Fallback de página inteira se a página ficar sem extrações

    Retorna manifesto completo para uso em etapas posteriores.
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
        for i, page in enumerate(pdf.pages, start=1):
            # Regra de negócio: pular SEMPRE a primeira página
            if i == 1:
                continue

            before_count = len(all_images)

            embedded = _try_extract_embedded_images(
                page=page,
                page_number=i,
                out_dir=out_dir,
                per_page_counter=per_page_count,
                logger=logger,
            )
            all_images.extend(embedded)

            rendered_crops = _extract_rendered_crops(
                page=page,
                page_number=i,
                out_dir=out_dir,
                per_page_counter=per_page_count,
                logger=logger,
                dpi=render_dpi,
            )
            all_images.extend(rendered_crops)

            # Se nada foi extraído da página, salva render inteiro
            if len(all_images) == before_count:
                fallback = _fallback_full_page_render(
                    page=page,
                    page_number=i,
                    out_dir=out_dir,
                    per_page_counter=per_page_count,
                    logger=logger,
                    dpi=render_dpi,
                )
                if fallback is not None:
                    all_images.append(fallback)

    manifest = ExtractionManifest(
        output_dir=str(out_dir.resolve()),
        total_images=len(all_images),
        images=all_images,
        per_page_count=per_page_count,
    )

    return manifest


def manifest_to_dict(manifest: ExtractionManifest) -> Dict:
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
