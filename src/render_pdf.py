"""任务 3：使用 PyMuPDF 将 PDF 候选页面渲染为图片。

策略：
- 优先渲染任务2给出的候选页窗口（缩小范围，不一次性渲染整本书）；
- 保存候选页缩略图到缓存；
- 最终匹配页用高分辨率重新渲染。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np

from . import config


def render_page(pdf_path, page_index: int, dpi: int = config.PDF_RENDER_DPI) -> np.ndarray:
    """渲染 PDF 单页为 BGR 图像（numpy 数组）。"""
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_index)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    # PyMuPDF 默认 RGB，转 BGR 供 OpenCV 使用
    if pix.n == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    elif pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    doc.close()
    return img


def render_candidate_pages(pdf_path, page_indices: list[int], dpi: int = config.PDF_RENDER_DPI,
                           save_thumbnails: bool = True, cache_dir=None) -> dict[int, np.ndarray]:
    """渲染多个候选页，返回 {page_index: image}。可选保存缩略图。"""
    out: dict[int, np.ndarray] = {}
    for idx in page_indices:
        img = render_page(pdf_path, idx, dpi)
        out[idx] = img
        if save_thumbnails and cache_dir is not None:
            thumb = cv2.resize(img, (img.shape[1] // 3, img.shape[0] // 3))
            path = Path(cache_dir) / f"thumb_page_{idx:03d}.png"
            _imwrite_unicode(path, thumb)
    return out


def render_matched_page_hi(pdf_path, page_index: int) -> np.ndarray:
    """最终匹配页高清渲染。"""
    return render_page(pdf_path, page_index, dpi=config.PDF_RENDER_DPI_HI)


def _imwrite_unicode(path: Path, img: np.ndarray) -> None:
    """支持中文路径的 imwrite。"""
    ok, buf = cv2.imencode(Path(path).suffix, img)
    if ok:
        Path(path).write_bytes(buf.tobytes())


if __name__ == "__main__":
    config.ensure_dirs()
    from .inspect_inputs import run as run_inspect
    info = run_inspect()
    pages = render_candidate_pages(config.PDF_PATH, info.preliminary_page_hints)
    print(f"已渲染 {len(pages)} 个候选页")
    for idx, img in pages.items():
        print(f"  page {idx}: {img.shape[1]} x {img.shape[0]}")
