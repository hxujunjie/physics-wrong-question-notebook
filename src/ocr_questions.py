"""题目索引与 OCR 边界识别。

干净练习册 PDF 是题目内容的唯一来源。这个模块只负责从 PDF 页面图像
识别题号、栏位和题目边界，并提供一个按需缓存的全书索引，供学生端和
教师端共用。
"""
from __future__ import annotations

import re
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass
class QuestionItem:
    qnum: str
    bbox: tuple[int, int, int, int]
    top_y: int
    bottom_y: int
    column: int
    page_index: int | None = None
    page_w: int = 0
    page_h: int = 0
    column_x0: int = 0
    column_x1: int = 0
    confidence: float = 0.0
    question_id: str | None = None
    is_page_tail: bool = False


@dataclass
class SourceSegment:
    """一道题在干净 PDF 中的一个连续图像片段。"""

    page_index: int
    bbox: tuple[int, int, int, int]
    is_continuation: bool = False


@dataclass
class OcrResult:
    questions: list[QuestionItem] = field(default_factory=list)
    ocr_lines: list = field(default_factory=list)
    success: bool = False
    warnings: list[str] = field(default_factory=list)
    page_index: int | None = None
    page_w: int = 0
    page_h: int = 0
    engine: str = "rapidocr"


_QNUM_PATTERN_START = re.compile(r"^\s*(\d{1,3})\s*[\.、\)）:]" )
_QNUM_PATTERN_PURE = re.compile(r"^\s*(\d{1,3})\s*$")
_QNUM_PATTERN_FULL = re.compile(r"^\s*第\s*(\d{1,3})\s*题")
_QNUM_PATTERN_DASH = re.compile(r"^\s*(\d{1,3}-\d{1,3})\s+")
_OPTION_PATTERN = re.compile(r"^\s*[A-DＡ-Ｄ]\s*[\.、\)）:]")


def _extract_qnum(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    for pattern in (
        _QNUM_PATTERN_START,
        _QNUM_PATTERN_FULL,
        _QNUM_PATTERN_DASH,
        _QNUM_PATTERN_PURE,
    ):
        match = pattern.match(text)
        if match:
            return match.group(1)
    return None


def _bbox_info(bbox) -> tuple[int, int, int, int]:
    xs = [float(p[0]) for p in bbox]
    ys = [float(p[1]) for p in bbox]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _ocr_page(img: np.ndarray, ocr_engine) -> list:
    result, _ = ocr_engine(img)
    if not result:
        return []
    lines = []
    for line in result:
        if len(line) < 3:
            continue
        lines.append((line[0], line[1], float(line[2])))
    return lines


def _looks_like_option(text: str) -> bool:
    return bool(_OPTION_PATTERN.match(text or ""))


def _looks_like_measurement(text: str) -> bool:
    match = _QNUM_PATTERN_START.match((text or "").strip())
    if not match:
        return False
    remainder = (text or "")[match.end() :].lstrip()
    return bool(re.match(r"^[\d.,＋+\-−=]", remainder))


def _column_split(centers: list[float], page_w: int) -> float:
    """根据题号中心动态计算双栏中线，避免依赖固定像素。"""
    if not centers:
        return page_w / 2
    left = [x for x in centers if x < page_w * 0.48]
    right = [x for x in centers if x > page_w * 0.52]
    if left and right:
        return (max(left) + min(right)) / 2
    return page_w / 2


def _layout_column_split(ocr_lines: list, page_w: int, page_h: int) -> float | None:
    """从整页文字分布判断双栏，覆盖“只有一栏出现题号”的页面。"""
    centers = []
    for bbox, _text, confidence in ocr_lines:
        if confidence < 0.45:
            continue
        _x1, y1, x2, _y2 = _bbox_info(bbox)
        if y1 < page_h * 0.06 or y1 > page_h * 0.92:
            continue
        centers.append((float(_x1 + x2) / 2, y1))
    xs = sorted(x for x, _y in centers)
    if len(xs) < 6:
        return None
    best_gap = 0.0
    best_split = None
    for left, right in zip(xs, xs[1:]):
        gap = right - left
        split = (left + right) / 2
        if page_w * 0.30 < split < page_w * 0.70 and gap > best_gap:
            best_gap = gap
            best_split = split
    if best_split is None or best_gap < page_w * 0.08:
        return None
    left_count = sum(1 for x in xs if x < best_split)
    right_count = len(xs) - left_count
    if left_count < 3 or right_count < 3:
        return None
    return best_split


def _nearby_body_line(
    raw_lines: list,
    bbox: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
) -> bool:
    """纯数字题号必须有附近正文，防止页脚页码变成题目。"""
    x1, y1, x2, y2 = bbox
    center_x = (x1 + x2) / 2
    split = page_w / 2
    col = 0 if center_x < split else 1
    for other_bbox, other_text, other_conf in raw_lines:
        if other_conf < 0.35 or _extract_qnum(other_text) is not None:
            continue
        ox1, oy1, ox2, oy2 = _bbox_info(other_bbox)
        if oy1 < y2 - 8 or oy1 > y2 + min(260, int(page_h * 0.14)):
            continue
        if (0 if (ox1 + ox2) / 2 < split else 1) != col:
            continue
        if abs((ox1 + ox2) / 2 - center_x) > page_w * 0.48:
            continue
        if other_text.strip():
            return True
    return False


def _is_footer_number(text: str, bbox: tuple[int, int, int, int], page_h: int) -> bool:
    if not _QNUM_PATTERN_PURE.match((text or "").strip()):
        return False
    _x1, y1, _x2, y2 = bbox
    return y1 >= page_h * 0.88 or y2 >= page_h * 0.96


def _is_row_leading_number(
    raw_lines: list,
    bbox: tuple[int, int, int, int],
    page_w: int,
) -> bool:
    """题号必须是本行最靠左的文字，不能是正文/选项中的数字。

    OCR 辅助 PDF 的文字层会把 ``0.2`` 拆成 ``0.`` 和 ``2.``。后者虽然
    符合题号正则，却位于同一行正文的中间；若把它当题号，题目会从中间被
    截断。真实题号应当位于题干这一行的起始位置。
    """
    x1, y1, x2, y2 = bbox
    height = max(1, y2 - y1)
    candidate_side = 0 if (x1 + x2) / 2 < page_w / 2 else 1
    for other_bbox, other_text, other_conf in raw_lines:
        if other_conf < 0.35 or not (other_text or "").strip():
            continue
        ox1, oy1, ox2, oy2 = _bbox_info(other_bbox)
        other_side = 0 if (ox1 + ox2) / 2 < page_w / 2 else 1
        if other_side != candidate_side:
            continue
        if ox1 >= x1 - 3:
            continue
        vertical_overlap = max(0, min(y2, oy2) - max(y1, oy1))
        same_row = vertical_overlap >= min(height, max(1, oy2 - oy1)) * 0.45
        if same_row:
            return False
    return True


def _recover_misread_question_markers(
    raw: list[tuple[tuple[int, int, int, int], str, float, str]],
    ocr_lines: list,
    page_w: int,
    split: float,
    has_both_columns: bool,
) -> None:
    """Recover a question number read as a punctuation glyph by OCR."""
    if not has_both_columns:
        return
    for column in (0, 1):
        known = sorted(
            (
                entry
                for entry in raw
                if ((entry[0][0] + entry[0][2]) / 2 < split) == (column == 0)
            ),
            key=lambda entry: entry[0][1],
        )
        numeric = [entry for entry in known if entry[1].isdigit()]
        if not numeric:
            continue
        anchor_x = int(round(float(np.median([entry[0][0] for entry in numeric]))))
        column_x1 = int(split) if column == 0 else page_w
        for bbox, text, confidence in ocr_lines:
            marker = (text or "").strip()
            if marker not in {"&", "B", "S", "$"} or confidence < 0.70:
                continue
            info = _bbox_info(bbox)
            x1, y1, x2, y2 = info
            if abs(x1 - anchor_x) > max(32, int(page_w * 0.025)):
                continue
            previous = [entry for entry in numeric if entry[0][1] < y1 - 35]
            if not previous:
                continue
            predecessor = previous[-1]
            try:
                inferred = str(int(predecessor[1]) + 1)
            except ValueError:
                continue
            if any(entry[1] == inferred and abs(entry[0][1] - y1) < 80 for entry in raw):
                continue
            has_body = False
            for body_bbox, body_text, body_conf in ocr_lines:
                if body_conf < 0.60 or len((body_text or "").strip()) < 4:
                    continue
                bx1, by1, _bx2, by2 = _bbox_info(body_bbox)
                overlap = max(0, min(y2, by2) - max(y1, by1))
                if overlap >= max(1, y2 - y1) * 0.35 and x2 - 5 <= bx1 < column_x1:
                    has_body = True
                    break
            if has_body:
                raw.append((info, inferred, min(float(confidence), 0.85), marker))


def _group_questions(ocr_lines: list, page_w: int, page_h: int) -> list[QuestionItem]:
    raw: list[tuple[tuple[int, int, int, int], str, float, str]] = []
    for bbox, text, conf in ocr_lines:
        if conf < 0.45 or _looks_like_option(text):
            continue
        if _looks_like_measurement(text):
            continue
        qnum = _extract_qnum(text)
        if qnum is None:
            continue
        # 孤立数字既可能是页码，也可能是表格数值或公式编号；自动导出时
        # 不把它当作正式题号，正式题号必须带有标点或“第…题”结构。
        if _QNUM_PATTERN_PURE.match((text or "").strip()):
            continue
        try:
            if not 0 < int(qnum.split("-")[0]) <= 200:
                continue
        except ValueError:
            continue
        info = _bbox_info(bbox)
        if _is_footer_number(text, info, page_h):
            continue
        if not _is_row_leading_number(ocr_lines, info, page_w):
            continue
        if _QNUM_PATTERN_PURE.match((text or "").strip()) and not _nearby_body_line(
            ocr_lines, info, page_w, page_h
        ):
            continue
        raw.append((info, str(qnum), conf, text))

    if not raw:
        return []

    layout_split = _layout_column_split(ocr_lines, page_w, page_h)
    split = layout_split or _column_split([(b[0] + b[2]) / 2 for b, *_ in raw], page_w)
    has_both_columns = layout_split is not None or (
        any((b[0] + b[2]) / 2 < split for b, *_ in raw)
        and any((b[0] + b[2]) / 2 >= split for b, *_ in raw)
    )
    _recover_misread_question_markers(raw, ocr_lines, page_w, split, has_both_columns)

    items: list[QuestionItem] = []
    for info, qnum, conf, _text in raw:
        x1, y1, x2, y2 = info
        center_x = (x1 + x2) / 2
        if not has_both_columns:
            column = -1
            col_x0, col_x1 = 0, page_w
        elif center_x < split:
            column = 0
            col_x0, col_x1 = 0, int(split)
        else:
            column = 1
            col_x0, col_x1 = int(split), page_w
        items.append(
            QuestionItem(
                qnum=qnum,
                bbox=info,
                top_y=max(0, y1 - 10),
                bottom_y=y2,
                column=column,
                page_w=page_w,
                page_h=page_h,
                column_x0=col_x0,
                column_x1=col_x1,
                confidence=round(float(conf), 4),
            )
        )

    # 同一题号在同一位置被 OCR 重复识别时保留置信度最高的一条。
    deduped: list[QuestionItem] = []
    for item in sorted(items, key=lambda q: (q.column, q.top_y, q.bbox[0])):
        duplicate = next(
            (
                old
                for old in deduped
                if old.column == item.column
                and old.qnum == item.qnum
                and abs(old.top_y - item.top_y) < 24
                and (
                    old.column == -1
                    or abs(old.bbox[0] - item.bbox[0]) < max(30, int(page_w * 0.04))
                )
            ),
            None,
        )
        if duplicate is None:
            deduped.append(item)
        elif item.confidence > duplicate.confidence:
            deduped[deduped.index(duplicate)] = item

    result: list[QuestionItem] = []
    for column in (-1, 0, 1):
        group = sorted((q for q in deduped if q.column == column), key=lambda q: q.top_y)
        for index, item in enumerate(group):
            if index + 1 < len(group):
                item.bottom_y = max(item.bbox[3] + 12, group[index + 1].top_y - 12)
            else:
                item.bottom_y = min(page_h, max(item.bbox[3] + 20, int(page_h * 0.94)))
            result.append(item)

    # 按阅读顺序返回：左栏从上到下，再右栏从上到下；单栏按 y 排序。
    result.sort(key=lambda q: ((q.column if q.column >= 0 else 0), q.top_y, q.bbox[0]))
    if result:
        last = result[-1]
        last.is_page_tail = last.bottom_y >= page_h * 0.90
    return result


_ocr_instance = None


def _get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        # RapidOCR 的 config.yaml 使用短模块名动态导入。源码环境中这些
        # 名称通常碰巧可用，PyInstaller 环境则可能先创建同名 namespace
        # package，导致模块存在但没有 TextDetector 等属性。显式导入完整
        # 包并注册短别名，确保源码和冻结环境走完全相同的初始化路径。
        for short_name in (
            "ch_ppocr_v3_det",
            "ch_ppocr_v3_rec",
            "ch_ppocr_v2_cls",
        ):
            module = importlib.import_module(
                f"rapidocr_onnxruntime.{short_name}"
            )
            sys.modules[short_name] = module
        from rapidocr_onnxruntime import RapidOCR

        _ocr_instance = RapidOCR()
    return _ocr_instance


def run(pdf_img: np.ndarray, page_index: int | None = None) -> OcrResult:
    warnings: list[str] = []
    h, w = pdf_img.shape[:2]
    try:
        ocr = _get_ocr()
        lines = _ocr_page(pdf_img, ocr)
    except Exception as exc:
        return OcrResult(
            success=False,
            warnings=[f"OCR 识别失败: {exc}"],
            page_index=page_index,
            page_w=w,
            page_h=h,
        )

    if not lines:
        return OcrResult(
            success=False,
            warnings=["未识别到任何文字"],
            ocr_lines=[],
            page_index=page_index,
            page_w=w,
            page_h=h,
        )
    questions = _group_questions(lines, w, h)
    if not questions:
        warnings.append("未识别到可靠题号")
    return OcrResult(
        questions=questions,
        ocr_lines=lines,
        success=bool(questions),
        warnings=warnings,
        page_index=page_index,
        page_w=w,
        page_h=h,
    )


def find_nearest_question(
    questions: list[QuestionItem], x: float, y: float, page_w: int | None = None
) -> Optional[QuestionItem]:
    """按题目栏位和 y 区间定位题目，不使用固定像素中线。"""
    if not questions:
        return None
    if page_w is None:
        page_w = next((q.page_w for q in questions if q.page_w), 0)
    candidates = []
    for question in questions:
        if question.column == -1:
            in_column = True
        else:
            in_column = question.column_x0 <= x <= question.column_x1
        if in_column:
            candidates.append(question)
    if not candidates:
        candidates = questions
    for question in candidates:
        if question.top_y <= y <= question.bottom_y:
            return question
    return min(
        candidates,
        key=lambda q: min(abs(y - q.top_y), abs(y - q.bottom_y)),
    )


class SourceBookIndex:
    """干净 PDF 的按需题目索引。

    只 OCR 红叉命中的页及其前后页，结果在一次学生批处理内复用；这样既能
    处理跨页题，也不会让每张照片重复 OCR 整本练习册。
    """

    def __init__(
        self,
        pdf_path: str | Path,
        dpi: int = 200,
        index_pdf_path: str | Path | None = None,
    ):
        self.pdf_path = str(pdf_path)
        self.dpi = dpi
        import fitz

        with fitz.open(self.pdf_path) as doc:
            self.page_count = doc.page_count
        self.index_pdf_path = str(index_pdf_path or pdf_path)
        source_path = Path(self.pdf_path).resolve()
        index_path = Path(self.index_pdf_path).resolve()
        self.uses_external_index = index_path != source_path
        if self.uses_external_index:
            if not index_path.exists():
                raise FileNotFoundError(f"OCR 辅助 PDF 不存在：{index_path}")
            with fitz.open(self.index_pdf_path) as doc:
                if doc.page_count != self.page_count:
                    raise ValueError(
                        "OCR 辅助 PDF 页数与原始 PDF 不一致，不能建立题目索引"
                    )
        self.images: dict[int, np.ndarray] = {}
        self.results: dict[int, OcrResult] = {}
        self.external_index_pages: set[int] = set()

    @staticmethod
    def _normalize_external_columns(
        questions: list[QuestionItem], page_w: int, page_h: int
    ) -> None:
        """OCR 文字层可能在栏间留出异常空隙，统一映射回原 PDF 中线。"""
        has_left = any((q.bbox[0] + q.bbox[2]) / 2 < page_w * 0.45 for q in questions)
        has_right = any((q.bbox[0] + q.bbox[2]) / 2 > page_w * 0.55 for q in questions)
        if not (has_left and has_right):
            return
        split = page_w // 2
        for question in questions:
            center_x = (question.bbox[0] + question.bbox[2]) / 2
            question.column = 0 if center_x < split else 1
            question.column_x0 = 0 if question.column == 0 else split
            question.column_x1 = split if question.column == 0 else page_w

        for column in (0, 1):
            group = sorted(
                (question for question in questions if question.column == column),
                key=lambda question: question.top_y,
            )
            for index, question in enumerate(group):
                if index + 1 < len(group):
                    question.bottom_y = max(
                        question.bbox[3] + 12, group[index + 1].top_y - 12
                    )
                else:
                    question.bottom_y = min(
                        page_h, max(question.bbox[3] + 20, int(page_h * 0.94))
                    )
        questions.sort(key=lambda q: (q.column, q.top_y, q.bbox[0]))
        if questions:
            questions[-1].is_page_tail = questions[-1].bottom_y >= page_h * 0.90

    def _external_pdf_text_result(
        self, page_index: int, image: np.ndarray
    ) -> OcrResult | None:
        """从 OCR 辅助 PDF 的可搜索文字层建立原始 PDF 的题目坐标。"""
        import fitz

        with fitz.open(self.index_pdf_path) as doc:
            page = doc.load_page(page_index)
            words = page.get_text("words", sort=True)
            rect = page.rect
        if not words or rect.width <= 0 or rect.height <= 0:
            return None

        page_h, page_w = image.shape[:2]
        scale_x = page_w / rect.width
        scale_y = page_h / rect.height
        lines = []
        for word in words:
            if len(word) < 5:
                continue
            x0, y0, x1, y1, text = word[:5]
            text = (text or "").strip()
            if not text:
                continue
            bbox = [
                [x0 * scale_x, y0 * scale_y],
                [x1 * scale_x, y0 * scale_y],
                [x1 * scale_x, y1 * scale_y],
                [x0 * scale_x, y1 * scale_y],
            ]
            lines.append((bbox, text, 0.99))

        questions = _group_questions(lines, page_w, page_h)
        if not questions:
            return None
        self._normalize_external_columns(questions, page_w, page_h)
        return OcrResult(
            questions=questions,
            ocr_lines=lines,
            success=True,
            page_index=page_index,
            page_w=page_w,
            page_h=page_h,
            engine="pdf-text",
        )

    def add_page(
        self,
        page_index: int,
        image: np.ndarray,
        ocr_result: OcrResult | None = None,
    ) -> OcrResult:
        if page_index not in self.images:
            self.images[page_index] = image
        if ocr_result is None:
            ocr_result = self._external_pdf_text_result(
                page_index, self.images[page_index]
            )
            if ocr_result is None:
                ocr_result = run(self.images[page_index], page_index=page_index)
            else:
                self.external_index_pages.add(page_index)
        else:
            ocr_result.page_index = page_index
        self.results[page_index] = ocr_result
        for order, question in enumerate(ocr_result.questions, start=1):
            question.page_index = page_index
            question.question_id = (
                f"p{page_index + 1:03d}-c{question.column}-"
                f"q{question.qnum}-n{order:02d}"
            )
        return ocr_result

    def ensure_pages(self, page_indices: list[int] | tuple[int, ...]) -> None:
        from . import render_pdf

        for page_index in sorted(set(page_indices)):
            if page_index < 0 or page_index >= self.page_count:
                continue
            if page_index not in self.results:
                image = render_pdf.render_page(self.pdf_path, page_index, dpi=self.dpi)
                self.add_page(page_index, image)
            elif page_index not in self.images:
                self.images[page_index] = render_pdf.render_page(
                    self.pdf_path, page_index, dpi=self.dpi
                )

    def release_images(self) -> None:
        """释放页面图像，只保留 OCR 边界结果供后续照片复用。"""
        self.images.clear()

    def page_image(self, page_index: int) -> np.ndarray | None:
        return self.images.get(page_index)

    def _content_column_bounds(
        self, page_index: int, column: int, question: QuestionItem | None = None
    ) -> tuple[int, int]:
        """返回去掉整页外边距后的统一正文栏边界。

        题号左边缘是练习册正文栏最稳定的锚点。双栏页面用左右题号锚点
        的间距作为统一栏周期，使左、右题块具有相同像素宽度；这样写入
        PDF 后字号和缩进保持一致，也不会把左侧整页空白带入错题集。
        """
        result = self.results.get(page_index)
        if result is None or result.page_w <= 0:
            return (0, 0)
        page_w = result.page_w
        questions = self.page_questions(page_index)
        padding = max(8, int(page_w * 0.007))
        left = [q for q in questions if q.column == 0]
        right = [q for q in questions if q.column == 1]

        if column == -1 and question is not None:
            # A source page can mix a true full-width question with ordinary
            # left-column questions while its OCR anchors are all on the left.
            # Read the question's own text band instead of exporting every
            # item at page width.
            y0 = max(0, question.bbox[1] - 8)
            y1 = min(result.page_h, question.bottom_y + 8)
            body_boxes = []
            for bbox, text, confidence in result.ocr_lines:
                if confidence < 0.45 or len((text or "").strip()) < 2:
                    continue
                bx1, by1, bx2, by2 = _bbox_info(bbox)
                if by2 < y0 or by1 > y1:
                    continue
                if _is_footer_number(text, (bx1, by1, bx2, by2), result.page_h):
                    continue
                body_boxes.append((bx1, bx2))
            start = max(0, question.bbox[0] - padding)
            has_right_body = any((bx1 + bx2) / 2 > page_w * 0.56 for bx1, bx2 in body_boxes)
            if has_right_body:
                return start, max(start + 100, min(page_w, page_w - start))
            end = max(start + 100, int(page_w * 0.5) - padding)
            return start, min(page_w, end)

        if left and right:
            left_anchor = int(round(float(np.median([q.bbox[0] for q in left]))))
            right_anchor = int(round(float(np.median([q.bbox[0] for q in right]))))
            period = right_anchor - left_anchor
            if period > page_w * 0.25:
                width = max(100, period - 2 * padding)
                start = (left_anchor if column == 0 else right_anchor) - padding
                start = max(0, min(start, page_w - width))
                return start, min(page_w, start + width)

        group = [q for q in questions if q.column == column]
        if not group:
            group = questions
        if not group:
            return (0, page_w)
        start = max(0, int(round(float(np.median([q.bbox[0] for q in group])))) - padding)
        if column >= 0:
            end = min(page_w, max(q.column_x1 for q in group) - padding)
        else:
            end = page_w - start
        if end - start < 100:
            return (0, page_w)
        return start, end

    def page_questions(self, page_index: int) -> list[QuestionItem]:
        result = self.results.get(page_index)
        return result.questions if result else []

    def find_question(self, page_index: int, x: float, y: float) -> QuestionItem | None:
        questions = self.page_questions(page_index)
        if questions:
            result = find_nearest_question(questions, x, y)
            # 红叉在页面顶部的续页区域时，优先交给上一页最后一道题。
            first = min(questions, key=lambda q: q.top_y)
            if (
                result is first
                and y < first.top_y - 12
                and page_index > 0
                and page_index - 1 in self.results
            ):
                previous = self.page_questions(page_index - 1)
                if previous:
                    return max(previous, key=lambda q: (q.column, q.top_y))
            return result
        if page_index > 0:
            previous = self.page_questions(page_index - 1)
            if previous:
                return max(previous, key=lambda q: (q.column, q.top_y))
        return None

    def _is_page_tail(self, question: QuestionItem) -> bool:
        questions = self.page_questions(question.page_index or 0)
        if not questions:
            return False
        last = max(questions, key=lambda q: (q.column, q.top_y))
        return last is question and question.bottom_y >= question.page_h * 0.90

    def _same_page_next_question(self, question: QuestionItem) -> QuestionItem | None:
        """按左栏到右栏的阅读流返回同页下一道正式题号。"""
        questions = sorted(
            self.page_questions(question.page_index or 0),
            key=lambda q: (q.column if q.column >= 0 else 0, q.top_y, q.bbox[0]),
        )
        for index, current in enumerate(questions):
            if current is question and index + 1 < len(questions):
                return questions[index + 1]
        return None

    def _same_page_right_continuation(
        self, question: QuestionItem, image: np.ndarray
    ) -> SourceSegment | None:
        """处理左栏底部题干接同页右栏顶部图/选项的跨栏续题。"""
        if question.column != 0 or question.bottom_y < question.page_h * 0.90:
            return None
        next_question = self._same_page_next_question(question)
        if next_question is None or next_question.column != 1:
            return None
        result = self.results.get(question.page_index or 0)
        if not self._has_continuation_text(result, next_question):
            return None

        height, width = image.shape[:2]
        # 不向左跨过分栏线；续片段只应包含右栏真正属于本题的内容。
        x0, x1 = self._content_column_bounds(question.page_index or 0, 1)
        x0 = max(0, x0)
        x1 = min(width, x1)
        # 右栏顶部可能含有章节标题、页眉等不属于这道题的内容。续题
        # 通常与左栏第一道正式题处于同一正文起始带；以该题号作为
        # 下界，既保留右栏首题的图片/选项，又不把章节标题拼进错题集。
        left_questions = [
            candidate
            for candidate in self.page_questions(question.page_index or 0)
            if candidate.column == 0
        ]
        body_top = min(
            (candidate.bbox[1] for candidate in left_questions),
            default=int(height * 0.12),
        )
        y0 = max(0, body_top - 18)
        y1 = min(height, max(y0 + 30, next_question.top_y - 10))
        if x1 <= x0 or y1 <= y0:
            return None
        return SourceSegment(
            question.page_index or 0,
            (x0, y0, x1 - x0, y1 - y0),
            is_continuation=True,
        )

    def question_segments(self, question: QuestionItem) -> list[SourceSegment]:
        """返回题目的完整页面片段，必要时追加下一页续页。"""
        page_index = question.page_index
        if page_index is None:
            return []
        self.ensure_pages([page_index, page_index + 1])
        image = self.images.get(page_index)
        if image is None:
            return []
        h, w = image.shape[:2]
        x0, x1 = self._content_column_bounds(page_index, question.column, question)
        x0 = max(0, x0)
        x1 = min(w, x1)
        # 起点以正式题号的实际文字框为准；top_y 是用于题间边界计算的
        # 扩展坐标，直接拿它裁剪可能把上一题最后一行带进来。
        y0 = max(0, question.bbox[1] - 8)
        y1 = min(h, question.bottom_y + 8)
        segments = [SourceSegment(page_index, (x0, y0, x1 - x0, y1 - y0))]

        same_page_continuation = self._same_page_right_continuation(question, image)
        if same_page_continuation is not None:
            # 左栏末题的默认 bottom_y 会延伸到页脚，以便覆盖真正的跨页题。
            # 对“左栏 -> 同页右栏”的题，正文文字已经给出可靠终点，不能把
            # 页码旁的点阵、页脚或分栏线作为题目的一部分导出。
            result = self.results.get(page_index)
            body_bottom = question.bbox[3]
            if result is not None:
                for bbox, text, confidence in result.ocr_lines:
                    if confidence < 0.35 or _extract_qnum(text) is not None:
                        continue
                    bx1, by1, bx2, by2 = _bbox_info(bbox)
                    center_x = (bx1 + bx2) / 2
                    if not (question.column_x0 <= center_x <= question.column_x1):
                        continue
                    if question.bbox[1] - 8 <= by1 < question.bottom_y:
                        body_bottom = max(body_bottom, by2)
            clean_y1 = min(h, body_bottom + 16)
            clean_x1 = x1
            segments[0] = SourceSegment(
                page_index,
                (x0, y0, clean_x1 - x0, max(1, clean_y1 - y0)),
            )
            segments.append(same_page_continuation)
        elif self._is_page_tail(question) and page_index + 1 < self.page_count:
            next_questions = self.page_questions(page_index + 1)
            next_image = self.images.get(page_index + 1)
            next_result = self.results.get(page_index + 1)
            if next_image is not None and next_result is not None and next_questions:
                nh, nw = next_image.shape[:2]
                # 新页可能先出现页眉、章节标题，甚至把上一题最后一行
                # 误识别成类似“36.”的题号。只使用第一个真正的正式题号
                # 作为续题终点，不能直接取 OCR 结果中的最小题号。
                first = self._first_formal_question(next_result, next_questions)
                if first is None:
                    return segments
                next_x0, next_x1 = self._content_column_bounds(
                    page_index + 1, first.column
                )
                next_x0 = max(0, next_x0)
                next_x1 = min(nw, next_x1)
                next_y1 = max(int(nh * 0.08), first.top_y - 10)
                next_y0 = self._continuation_crop_start(next_result, first)
                if (
                    next_y0 is not None
                    and self._has_continuation_text(next_result, first)
                    and next_y1 > next_y0 + 30
                    and next_x1 > next_x0
                ):
                    segments.append(
                        SourceSegment(
                            page_index + 1,
                            (next_x0, next_y0, next_x1 - next_x0, next_y1 - next_y0),
                            is_continuation=True,
                        )
                    )
        return segments

    @staticmethod
    def _is_header_or_section_text(text: str) -> bool:
        """判断 OCR 行是否属于页眉、章节标题或题型标题。"""
        compact = re.sub(r"\s+", "", (text or "").strip())
        if not compact:
            return True
        if _QNUM_PATTERN_PURE.match(compact):
            return True
        if re.match(
            r"^(?:第\s*\d+\s*[章节]|第[一二三四五六七八九十百]+[章节]|"
            r"课时\s*\d+|[一二三四五六七八九十百]+[、.])",
            compact,
        ):
            return True
        return any(
            token in compact
            for token in (
                "导学案",
                "高中物理",
                "必修",
                "选择题",
                "填空题",
                "解答题",
                "非选择题",
            )
        )

    @classmethod
    def _continuation_body_lines(
        cls,
        result: OcrResult | None,
        first_question: QuestionItem | None,
    ) -> list[tuple[tuple[int, int, int, int], str, float]]:
        """返回新页中位于下一正式题号之前的有效续题正文行。"""
        if result is None or first_question is None or result.page_h <= 0:
            return []
        top_limit = first_question.top_y - 12
        if top_limit <= 0:
            return []

        # 页眉通常在页面前 8% 内；正文第一行可能紧贴该区域下方，
        # 因此只排除页眉带，不再从固定 4% 处直接裁剪。
        header_floor = int(result.page_h * 0.08)
        lines: list[tuple[tuple[int, int, int, int], str, float]] = []
        for bbox, text, confidence in result.ocr_lines:
            text = (text or "").strip()
            if confidence < 0.45 or not text:
                continue
            info = _bbox_info(bbox)
            x1, y1, x2, y2 = info
            center_x = (x1 + x2) / 2
            if y2 <= header_floor or y1 >= top_limit:
                continue
            if not (first_question.column_x0 <= center_x <= first_question.column_x1):
                continue
            if _is_footer_number(text, info, result.page_h):
                continue
            if cls._is_header_or_section_text(text):
                continue
            # 下一道题的题号不是上一道题的正文；这也过滤了 OCR 把
            # 上一道题末尾的“36.”误识别成独立题号的情况。
            if _extract_qnum(text) is not None:
                continue
            if len(text) < 2 and not _looks_like_option(text):
                continue
            lines.append((info, text, confidence))
        return sorted(lines, key=lambda entry: (entry[0][1], entry[0][0]))

    @classmethod
    def _continuation_crop_start(
        cls,
        result: OcrResult | None,
        first_question: QuestionItem | None,
    ) -> int | None:
        lines = cls._continuation_body_lines(result, first_question)
        if not lines or result is None:
            return None
        first_y = min(info[1] for info, _text, _confidence in lines)
        padding = max(8, int(result.page_w * 0.007))
        return max(0, first_y - padding)

    @classmethod
    def _first_formal_question(
        cls,
        result: OcrResult | None,
        questions: list[QuestionItem],
    ) -> QuestionItem | None:
        """跳过页眉和孤立题号，找到新页阅读流中的第一个正式题目。"""
        if result is None:
            return None
        for question in sorted(
            questions,
            key=lambda q: (q.column if q.column >= 0 else 0, q.top_y, q.bbox[0]),
        ):
            anchor_text = ""
            for bbox, text, _confidence in result.ocr_lines:
                info = _bbox_info(bbox)
                if max(abs(info[i] - question.bbox[i]) for i in range(4)) <= 3:
                    anchor_text = (text or "").strip()
                    break
            if not anchor_text or cls._is_header_or_section_text(anchor_text):
                continue
            if _looks_like_measurement(anchor_text):
                continue
            # OCR 将“36.”与后面的“9”“m”拆开时，不能把它当作正式题号。
            # 独立题号必须在同一行右侧有足够的正文支持。
            if re.match(r"^\d{1,3}\s*[.、)）]?$", anchor_text):
                has_same_row_body = False
                for bbox, text, _confidence in result.ocr_lines:
                    other = (text or "").strip()
                    if not other or other == anchor_text:
                        continue
                    info = _bbox_info(bbox)
                    overlap = max(
                        0,
                        min(question.bbox[3], info[3])
                        - max(question.bbox[1], info[1]),
                    )
                    if overlap < min(
                        question.bbox[3] - question.bbox[1], info[3] - info[1]
                    ) * 0.45:
                        continue
                    if info[0] < question.bbox[2] - 3:
                        continue
                    center_x = (info[0] + info[2]) / 2
                    if not (
                        question.column_x0 <= center_x <= question.column_x1
                    ):
                        continue
                    if len(other) >= 3 and not _looks_like_measurement(other):
                        has_same_row_body = True
                        break
                if not has_same_row_body:
                    continue
            return question
        return None

    @classmethod
    def _has_continuation_text(
        cls,
        result: OcrResult | None, first_question: QuestionItem | None
    ) -> bool:
        return bool(cls._continuation_body_lines(result, first_question))

    def extract_segments(self, segments: list[SourceSegment]) -> list[np.ndarray]:
        images: list[np.ndarray] = []
        for segment in segments:
            image = self.images.get(segment.page_index)
            if image is None:
                continue
            x, y, width, height = segment.bbox
            crop = image[y : y + height, x : x + width]
            if crop.size:
                images.append(crop.copy())
        return images
