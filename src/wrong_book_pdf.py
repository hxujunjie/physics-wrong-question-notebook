"""生成内容完整的学生错题集 PDF。"""
from __future__ import annotations

import datetime
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


@dataclass
class QuestionSegment:
    page_index: int | None
    image: np.ndarray
    is_continuation: bool = False


@dataclass
class WrongBookItem:
    qnum: str | None
    mark_type: str = "cross"
    page_index: int | None = None
    source_text: str | None = None
    occurrence_count: int = 1
    image: np.ndarray | None = None
    segments: list[QuestionSegment] = field(default_factory=list)
    source_question_id: str | None = None
    source_pages: list[int] = field(default_factory=list)

    def normalized_segments(self) -> list[QuestionSegment]:
        if self.segments:
            return [segment for segment in self.segments if segment.image is not None]
        if self.image is not None:
            return [QuestionSegment(self.page_index, self.image)]
        return []


def _source_sort_key(item: WrongBookItem, original_index: int) -> tuple[int, int, int, int, str]:
    """按原练习册阅读顺序排序：页码、左栏/右栏、栏内顺序。"""
    source_id = item.source_question_id or ""
    page = item.page_index
    if page is None:
        pages = [segment.page_index for segment in item.normalized_segments()]
        page = next((value for value in pages if value is not None), 10**9)

    column_match = re.search(r"-c(-?\d+)-", source_id)
    order_match = re.search(r"-n(\d+)$", source_id)
    qnum_match = re.search(r"(?:第\s*)?(\d+)", str(item.qnum or ""))
    column = int(column_match.group(1)) if column_match else 0
    if column < 0:
        column = 0
    order = int(order_match.group(1)) if order_match else (
        int(qnum_match.group(1)) if qnum_match else 10**9
    )
    qnum_text = str(item.qnum or "")
    return (int(page), column, order, original_index, qnum_text)


def _font_file() -> str | None:
    candidates = [
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts" / "simsun.ttc",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts" / "simhei.ttf",
    ]
    return next((str(path) for path in candidates if path.exists()), None)


def _fit_image(image: np.ndarray, max_width: float, max_height: float) -> np.ndarray:
    """按可用区域等比缩放，绝不裁掉题目内容。"""
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        return image
    scale = min(max_width / width, max_height / height, 1.0)
    if scale >= 1.0:
        return image
    return cv2.resize(
        image,
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _scaled_size(image: np.ndarray, max_width: float, max_height: float = float("inf")) -> tuple[float, float]:
    """返回图片等比缩放后的尺寸，供栏布局在写入 PDF 前预先测量。"""
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        return 0.0, 0.0
    scale = min(max_width / width, max_height / height, 1.0)
    return float(width * scale), float(height * scale)


def _is_full_width_item(item: WrongBookItem) -> bool:
    """Return true for a source crop that must span both output columns."""
    if "-c-1-" not in (item.source_question_id or ""):
        return False
    segments = item.normalized_segments()
    if not segments:
        return False
    height, width = segments[0].image.shape[:2]
    # After source-side blank-half trimming, a wide and short crop is a real
    # full-width question rather than a normal left-column question.
    return height > 0 and width / height >= 1.35


def _write_text(page, point, text: str, fontsize: float = 12, color=(0, 0, 0), fontfile=None):
    x, y = point
    if fontfile:
        try:
            page.insert_text(
                (x, y), text, fontsize=fontsize, fontname="cn_font", fontfile=fontfile, color=color
            )
            return
        except Exception:
            pass
    page.insert_text((x, y), text, fontsize=fontsize, fontname="china-s", color=color)


def _insert_image_at(
    page,
    image: np.ndarray,
    x0: float,
    y0: float,
    max_width: float,
    max_height: float,
    temp_paths: list[str],
) -> tuple[bool, float, float]:
    """在指定的连续排版位置插入完整题目片段。

    PDF 的版面单位是 pt，而 OpenCV 图像尺寸是 px；两者不能混用。
    这里只计算显示的 pt 尺寸，始终嵌入原始 PNG 像素，避免把高分辨率
    题目图预先缩成约 250 px 后再放大到一栏，造成 72 DPI 的模糊输出。
    """
    if image is None or image.size == 0:
        return False, 0.0, 0.0
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        return False, 0.0, 0.0
    display_scale = min(max_width / width, max_height / height, 1.0)
    display_width = float(width * display_scale)
    display_height = float(height * display_scale)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        return False, 0.0, 0.0
    temp_path = os.path.join(tempfile.gettempdir(), f"wrong_book_{os.getpid()}_{len(temp_paths)}.png")
    Path(temp_path).write_bytes(encoded.tobytes())
    temp_paths.append(temp_path)
    placed_x = x0
    page.insert_image(
        (placed_x, y0, placed_x + display_width, y0 + display_height),
        filename=temp_path,
    )
    return True, display_width, display_height


def validate_wrong_book_pdf(output_path: str | Path, expected_content_pages: int) -> tuple[bool, str]:
    """重新打开并校验生成的 PDF，避免把损坏文件交给用户。"""
    try:
        import fitz

        with fitz.open(str(output_path)) as doc:
            if doc.page_count != expected_content_pages + 1:
                return False, f"PDF 页数异常：{doc.page_count}"
            if expected_content_pages <= 0:
                return False, "没有可导出的完整题目"
            for page_index in range(1, doc.page_count):
                if not doc.load_page(page_index).get_images(full=True):
                    return False, f"第 {page_index + 1} 页没有题目图像"
        return True, ""
    except Exception as exc:
        return False, f"PDF 校验失败：{exc}"


def generate_wrong_book_pdf(
    items: list[WrongBookItem],
    output_path: str,
    student_name: str = "学生",
    title: str = "物理错题集",
) -> bool:
    """生成封面加 A4 双栏流式内容页的错题集 PDF。"""
    normalized_items = [item for item in items if item.normalized_segments()]
    normalized_items = [
        item
        for _, item in sorted(
            enumerate(normalized_items),
            key=lambda pair: _source_sort_key(pair[1], pair[0]),
        )
    ]
    if not normalized_items:
        return False
    try:
        import fitz

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output.with_name(output.name + ".tmp")
        temp_output.unlink(missing_ok=True)
        doc = fitz.open()
        page_w, page_h = 595, 842
        fontfile = _font_file()
        temp_paths: list[str] = []

        # 内容页采用与原练习册一致的双栏阅读流：左栏从上到下，
        # 再进入右栏，最后进入下一页。题目图片只做等比缩放。
        content_pages = 0
        horizontal_margin = 32.0
        column_gap = 18.0
        column_width = (page_w - horizontal_margin * 2 - column_gap) / 2
        column_top = 38.0
        column_bottom = page_h - 30.0
        column_height = column_bottom - column_top
        question_header_height = 20.0
        segment_gap = 5.0
        question_gap = 13.0

        cover = doc.new_page(width=page_w, height=page_h)
        _write_text(cover, (page_w / 2 - max(80, len(title) * 8), 300), title, 28, fontfile=fontfile)
        _write_text(cover, (page_w / 2 - 55, 350), f"学生：{student_name}", 16, (0.3, 0.3, 0.3), fontfile)
        _write_text(
            cover,
            (page_w / 2 - 75, 380),
            f"生成日期：{datetime.date.today():%Y-%m-%d}",
            13,
            (0.4, 0.4, 0.4),
            fontfile,
        )
        _write_text(
            cover,
            (page_w / 2 - 65, 410),
            f"共 {len(normalized_items)} 道错题",
            14,
            (0.4, 0.4, 0.4),
            fontfile,
        )

        current_page = None
        current_column = 0
        current_y = column_top

        def column_x(column: int) -> float:
            return horizontal_margin + column * (column_width + column_gap)

        def new_content_page():
            nonlocal content_pages
            nonlocal current_page, current_column, current_y
            content_pages += 1
            current_page = doc.new_page(width=page_w, height=page_h)
            current_column = 0
            current_y = column_top
            _write_text(
                current_page,
                (horizontal_margin, 19),
                title,
                8,
                (0.45, 0.45, 0.45),
                fontfile,
            )
            _write_text(
                current_page,
                (page_w - horizontal_margin - 70, 19),
                student_name,
                8,
                (0.45, 0.45, 0.45),
                fontfile,
            )
            current_page.draw_line(
                fitz.Point(horizontal_margin, 25),
                fitz.Point(page_w - horizontal_margin, 25),
                color=(0.82, 0.82, 0.82),
                width=0.5,
            )
            divider_x = page_w / 2
            current_page.draw_line(
                fitz.Point(divider_x, column_top),
                fitz.Point(divider_x, column_bottom),
                color=(0.86, 0.86, 0.86),
                width=0.45,
            )
            _write_text(
                current_page,
                (page_w / 2 - 3, page_h - 12),
                str(content_pages),
                7,
                (0.5, 0.5, 0.5),
                fontfile,
            )

        def ensure_content_page() -> None:
            if current_page is None:
                new_content_page()

        def advance_column() -> None:
            nonlocal current_column, current_y
            ensure_content_page()
            if current_column == 0:
                current_column = 1
                current_y = column_top
            else:
                new_content_page()

        def draw_question_header(
            item: WrongBookItem,
            item_index: int,
            continuation: bool,
            x: float | None = None,
            width: float | None = None,
        ) -> None:
            nonlocal current_y
            qnum = item.qnum or str(item_index)
            suffix = "（续）" if continuation else ""
            x = column_x(current_column) if x is None else x
            width = column_width if width is None else width
            _write_text(
                current_page,
                (x, current_y + 11),
                f"第 {qnum} 题{suffix}",
                10.5,
                fontfile=fontfile,
            )
            source_page = item.page_index
            if source_page is None:
                source_page = next(
                    (segment.page_index for segment in item.normalized_segments() if segment.page_index is not None),
                    None,
                )
            source_parts = []
            if source_page is not None:
                source_parts.append(f"第 {source_page + 1} 页")
            if item.occurrence_count > 1:
                source_parts.append(f"出现 {item.occurrence_count} 次")
            if source_parts:
                _write_text(
                    current_page,
                    (x + 76, current_y + 10),
                    " · ".join(source_parts),
                    7,
                    (0.45, 0.45, 0.45),
                    fontfile,
                )
            mark_label = "做错" if item.mark_type == "cross" else "待复核"
            mark_color = (0.8, 0, 0) if item.mark_type == "cross" else (0.7, 0.35, 0)
            _write_text(
                current_page,
                (x + width - 28, current_y + 10),
                mark_label,
                7,
                mark_color,
                fontfile,
            )
            current_page.draw_line(
                fitz.Point(x, current_y + 16),
                fitz.Point(x + width, current_y + 16),
                color=(0.88, 0.88, 0.88),
                width=0.35,
            )
            current_y += question_header_height

        def measured_segment_height(segment: QuestionSegment) -> float:
            return _scaled_size(segment.image, column_width)[1]

        def measured_question_height(item: WrongBookItem) -> float:
            segments = item.normalized_segments()
            return (
                question_header_height
                + sum(measured_segment_height(segment) for segment in segments)
                + segment_gap * max(0, len(segments) - 1)
                + question_gap
            )

        def place_segment(
            segment: QuestionSegment,
            available_height: float,
            x: float | None = None,
            width: float | None = None,
        ) -> float:
            nonlocal current_y
            x = column_x(current_column) if x is None else x
            width = column_width if width is None else width
            ok, _placed_width, placed_height = _insert_image_at(
                current_page,
                segment.image,
                x,
                current_y,
                width,
                max(24.0, available_height),
                temp_paths,
            )
            if not ok:
                raise RuntimeError("题目图片写入失败")
            current_y += placed_height
            return placed_height

        ensure_content_page()

        for item_index, item in enumerate(normalized_items, start=1):
            segments = item.normalized_segments()
            if _is_full_width_item(item):
                span_width = page_w - horizontal_margin * 2
                # A true source-page-wide question must not be reduced to a
                # half-column.  It starts on its own clean content page; the
                # following item resumes ordinary two-column flow.
                if current_y > column_top + 0.5 or current_column != 0:
                    new_content_page()
                draw_question_header(
                    item,
                    item_index,
                    continuation=False,
                    x=horizontal_margin,
                    width=span_width,
                )
                for segment_index, segment in enumerate(segments):
                    place_segment(
                        segment,
                        column_bottom - current_y,
                        x=horizontal_margin,
                        width=span_width,
                    )
                    if segment_index + 1 < len(segments):
                        current_y += segment_gap
                current_y += question_gap
                if item_index < len(normalized_items):
                    new_content_page()
                continue
            total_height = measured_question_height(item)
            remaining_height = column_bottom - current_y

            # 能在完整一栏中放下的题目作为一个整体移动，避免题干与选项
            # 因栏底剩余空间不足而被无意义拆开。
            if total_height <= column_height and total_height > remaining_height:
                advance_column()

            if total_height <= column_height:
                draw_question_header(item, item_index, continuation=False)
                for segment_index, segment in enumerate(segments):
                    place_segment(segment, column_bottom - current_y)
                    if segment_index + 1 < len(segments):
                        current_y += segment_gap
                current_y += question_gap
                continue

            # 超过一整栏的题从新栏顶部开始，并只在原始片段边界续栏。
            if current_y > column_top + 0.5:
                advance_column()
            draw_question_header(item, item_index, continuation=False)
            for segment_index, segment in enumerate(segments):
                natural_height = measured_segment_height(segment)
                remaining_height = column_bottom - current_y
                if natural_height > remaining_height and current_y > column_top + question_header_height + 0.5:
                    advance_column()
                    draw_question_header(item, item_index, continuation=True)
                    remaining_height = column_bottom - current_y
                place_segment(segment, remaining_height)
                if segment_index + 1 < len(segments):
                    next_height = measured_segment_height(segments[segment_index + 1])
                    if current_y + segment_gap + next_height > column_bottom:
                        advance_column()
                        draw_question_header(item, item_index, continuation=True)
                    else:
                        current_y += segment_gap
            current_y += question_gap

        # 内容页已由全局栏流排版器生成；不会再为每道题单独创建页面。

        doc.save(str(temp_output))
        doc.close()
        valid, _reason = validate_wrong_book_pdf(temp_output, content_pages)
        if not valid:
            temp_output.unlink(missing_ok=True)
            return False
        temp_output.replace(output)
        return True
    except Exception:
        try:
            if "doc" in locals() and not doc.is_closed:
                doc.close()
        except Exception:
            pass
        return False
    finally:
        for temp_path in locals().get("temp_paths", []):
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass
