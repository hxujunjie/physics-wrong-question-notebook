"""项目配置：常量与阈值。

路径参数（PDF、照片、输出目录）改为运行时传入，不再硬编码。
算法参数（阈值、DPI、特征点数量等）保持不变。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
PDF_PATH: Path = PROJECT_ROOT / "导学案-物理必修一-练习题.pdf"
PHOTO_PATH: Path = PROJECT_ROOT / "970d3d5fd7ecb934754608327358595c.jpg"
OUTPUT_DIR: Path = PROJECT_ROOT / "output"
DOCS_DIR: Path = PROJECT_ROOT / "docs"
CACHE_DIR: Path = OUTPUT_DIR / "_cache"

# 固定输出文件名（与任务要求一致）
OUT_ORIGINAL_PHOTO = "01_original_photo.jpg"
OUT_PAGE_BOUNDARY = "02_detected_page_boundary.jpg"
OUT_RECTIFIED = "03_rectified_page.jpg"
OUT_PDF_CANDIDATE = "04_pdf_candidate_page.jpg"
OUT_FEATURE_MATCHES = "05_feature_matches.jpg"
OUT_ALIGNMENT_OVERLAY = "06_alignment_overlay.jpg"
OUT_RED_MASK = "07_red_mask.png"
OUT_DETECTED_MARKS = "08_detected_marks.jpg"
OUT_MARKS_ON_PDF = "09_marks_on_clean_pdf.jpg"
OUT_RESULT_JSON = "result.json"
OUT_REPORT_MD = "technical_validation_report.md"

# PDF 渲染参数
PDF_RENDER_DPI = 200          # 候选页渲染 DPI（兼顾清晰度与速度）
PDF_RENDER_DPI_HI = 300       # 最终匹配页高清渲染 DPI
PDF_MATCH_DPI = 200           # 流式特征匹配仍使用清晰图像，避免错配页面
PDF_MAX_CANDIDATE_PAGES = 200  # 安全上限，避免一次性渲染过多

# 特征匹配参数
MATCHER = "flann"             # flann | bf
SIFT_FEATURES = 1500
MATCH_RATIO = 0.75           # Lowe ratio
MIN_GOOD_MATCHES = 15         # 低于此值视为不可靠
MIN_INLIER_RATIO = 0.30       # RANSAC 内点比例下限
RANSAC_REPROJ_THRESHOLD = 5.0

# 红色笔迹 HSV 阈值（OpenCV: H 0-179, S 0-255, V 0-255）
# 红色在 HSV 色相环两端，需两段区间
RED_HSV_LOWER_1 = (0, 70, 50)
RED_HSV_UPPER_1 = (12, 255, 255)
RED_HSV_LOWER_2 = (168, 70, 50)
RED_HSV_UPPER_2 = (180, 255, 255)

# 标记分类阈值
MIN_MARK_AREA = 80           # 小于此面积视为噪声
MAX_MARK_AREA_RATIO = 0.02   # 超过页面面积此比例视为异常
CROSS_CONF_THRESHOLD = 0.45  # 低于此置信度 -> unknown
CHECK_CONF_THRESHOLD = 0.45

# 候选题目裁取参数
QUESTION_PAD_X = 12          # 水平 padding（像素，PDF 渲染坐标）
QUESTION_PAD_TOP = 18
QUESTION_PAD_BOTTOM = 90     # 题目下方多裁一些以包含选项/小问
EXPORT_CROSS_CONFIDENCE = 0.50
EXPORT_QUESTION_CONFIDENCE = 0.45
INFER_UNCHECKED_AS_WRONG = True


def ensure_dirs(output_dir: Path | None = None) -> tuple[Path, Path]:
    """创建所有必要的输出目录。"""
    output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    cache_dir = output_dir / "_cache"
    docs_dir = output_dir.parent / "docs" if output_dir.name == "output" else output_dir / "docs"
    for d in (output_dir, docs_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)

    return cache_dir, docs_dir
