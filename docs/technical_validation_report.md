# 物理练习册错题自动整理系统 - 第一阶段技术验证报告

生成时间：2026-07-12 21:29:25

## 1. 输入文件信息

- PDF：`D:\Workspace\01_代码项目\活跃项目\physics-wrong-book\导学案-物理必修一-练习题.pdf`
- PDF 总页数：67
- PDF 首页尺寸(pt)：(609.42, 858.81) (1pt=1/72 inch)
- 照片：`D:\Workspace\01_代码项目\活跃项目\physics-wrong-book\970d3d5fd7ecb934754608327358595c.jpg`
- 照片是否成功读取：True
- 照片尺寸(px)：1279 × 1707
- 初步候选 PDF 页(0-based)：[59, 60, 61, 62, 63, 64, 65, 66]
- 原始素材 SHA256：PDF=`7dbdc1add724d374...` 照片=`d71efcaf52f324ce...`
- 原始素材是否保持不变：**True**

## 2. 使用的算法

- PDF 渲染：PyMuPDF (`fitz`)，候选页 200 DPI
- 书页检测与透视矫正：OpenCV 自适应阈值 + `findContours` + `approxPolyDP` + `warpPerspective`
- 实际矫正方法：四角透视矫正 (cv2.findContours + approxPolyDP + warpPerspective)
- 特征匹配：SIFT（OpenCV 5.x 主模块，免费）+ FLANN + Lowe 比率 + RANSAC Homography
- 红色笔迹提取：HSV 两段红色区间 + 形态学开闭运算 + 连通域过滤
- HSV 阈值：low=[(0, 70, 50)-(12, 255, 255)] + high=[(168, 70, 50)-(180, 255, 255)]
- 标记分类：骨架端点/交叉点 + HoughLinesP（角度聚类去重）+ 线段相交判定 + 长宽比 + Hu 矩
- 坐标映射：单应矩阵 H（照片→PDF）+ 重投影误差评估
- 候选题目裁取：按栏位+红叉位置启发式裁取，跨栏输出多候选

## 3. 最佳匹配 PDF 页面

- **匹配页（0-based 索引）：65**
- 对应书页码（1-based）：66
- 页面匹配置信度：1.0
- 匹配成功：True
- 各候选页匹配分数：

| 页索引 | good_matches | inliers | inlier_ratio | score |
|---|---|---|---|---|
| 65 | 149 | 60 | 0.4027 | 94.55 |
| 61 | 83 | 29 | 0.3494 | 49.06 |
| 59 | 75 | 23 | 0.3067 | 41.53 |
| 63 | 72 | 18 | 0.2500 | 36.0 |
| 60 | 67 | 18 | 0.2687 | 34.73 |
| 66 | 70 | 12 | 0.1714 | 28.98 |
| 62 | 75 | 10 | 0.1333 | 27.39 |
| 64 | 45 | 10 | 0.2222 | 21.21 |

- 人工参考页码：66（仅作对照，算法独立完成匹配，未硬编码）
- 算法匹配结果与人工参考一致
- 文字层核验：该页 PDF 文字层为乱码（自定义字体编码），印证了「不能依赖 PDF 文字层」，特征匹配为正确路线

## 4. 页面匹配置信度

- 综合置信度：1.0
- 最小 good_matches 阈值：15
- 最小内点比例阈值：0.3
- 最佳页 good_matches：149
- 最佳页内点比例：0.4027

## 5. 红色标记数量

- 检测到红色标记：6 个
- cross：2
- check：4
- unknown：0

## 6. 每个标记的分类和置信度

| # | type | confidence | bbox(x,y,w,h) | 端点 | 交叉点 | 方向组 | 相交 | reason |
|---|---|---|---|---|---|---|---|---|
| 0 | cross | 0.722 | (504, 1424, 186, 202) | 5 | 11 | 2 | 0 | cross_score=0.65 > check_score=0.25 |
| 1 | cross | 0.526 | (983, 1350, 209, 194) | 2 | 0 | 2 | 1 | cross_score=0.50 > check_score=0.45 |
| 2 | check | 0.85 | (580, 1121, 218, 204) | 2 | 0 | 3 | 0 | check_score=0.85 > cross_score=0.15 |
| 3 | check | 0.545 | (979, 696, 263, 216) | 2 | 0 | 2 | 1 | check_score=0.60 > cross_score=0.50 |
| 4 | check | 1.0 | (571, 575, 302, 140) | 2 | 0 | 4 | 0 | check_score=1.00 > cross_score=0.00 |
| 5 | check | 1.0 | (531, 223, 260, 131) | 2 | 0 | 4 | 0 | check_score=1.00 > cross_score=0.00 |

## 7. 红叉映射后的 PDF 坐标

| mark# | type | photo_anchor | pdf_anchor |
|---|---|---|---|
| 0 | cross | [593.1, 1519.9] | [800.6, 2192.5] |
| 1 | cross | [1068.9, 1456.6] | [1394.6, 2089.1] |
| 2 | check | [653.0, 1238.5] | [863.0, 1840.4] |
| 3 | check | [1088.3, 811.1] | [1424.8, 1262.8] |
| 4 | check | [706.1, 655.4] | [905.4, 1045.3] |
| 5 | check | [662.0, 299.1] | [822.6, 503.8] |

- 重投影误差（中位数，px）：1.992851974805897
- 配准置信度：0.917
- 配准成功：True

## 8. 裁取出的候选题目

| # | bbox(x,y,w,h) | 跨栏 | 对应红叉 | 文件 | reason |
|---|---|---|---|---|---|
| 0 | (0, 1490, 858, 792) | False | 0 | candidate_question_01.png | 红叉位于左栏，向上取约1题高度作为主候选 |
| 1 | (834, 95, 859, 658) | True | 0 | candidate_question_02.png | 红叉靠近左栏底部，题目可能横跨到右栏顶部，输出右栏顶部作为补充候选 |
| 2 | (834, 1387, 859, 792) | False | 1 | candidate_question_03.png | 红叉位于右栏，向上取约1题高度作为主候选 |

说明：候选题目均从干净 PDF 裁取，不含学生笔迹与教师批注。
本阶段未建立全书题目坐标数据库，故输出为「候选区域」，边界不确定时输出多候选。

## 9. 每个阶段是否成功

| 阶段 | 状态 |
|---|---|
| 任务2 输入检查 | 成功 |
| 任务3 PDF渲染 | 成功 |
| 任务4 书页矫正 | 成功 |
| 任务5 页面匹配 | 成功 |
| 任务6 红色提取 | 成功 |
| 任务7 标记分类 | 成功 |
| 任务8 坐标映射 | 成功 |
| 任务9 候选裁取 | 成功 |
| 任务10 报告生成 | 成功 |

## 10. 当前失败点和风险

- 红叉0靠近左栏底部，疑似跨栏题，已输出右栏顶部补充候选
- 红叉1靠近右栏底部，题目可能跨页，本阶段不跨页裁取
- 书页弯曲/阴影/透视变形可能导致四角检测不稳定；本例已尽量用特征配准补偿。
- 红叉/红勾分类基于规则，对笔画粗细、书写风格敏感，置信度不足时已输出 unknown。
- 候选题目边界为启发式估计，可能包含相邻题目或截断长题；跨栏题已输出多候选。
- 未做整本 PDF 题目分割，无法保证题目边界精确。
- 重投影误差受照片与 PDF 渲染分辨率差异影响。

## 11. 未调用在线 AI API 的证明/说明

- 本项目仅使用本地库：OpenCV、PyMuPDF、NumPy、Pillow、scikit-image。
- 全程无任何 HTTP/网络请求代码：未使用 `requests`、`urllib`、`httpx` 等访问外部服务。
- SIFT 为 OpenCV 内置本地算法（专利已过期），非在线服务。
- 未使用 OpenAI / Claude / Gemini / 任何在线大模型 / 付费 OCR。
- 所有输入素材仅在本地读取，未上传。
- `result.json` 中 `online_ai_used = False`

## 12. 下一阶段建议

1. 建立全书题目坐标数据库（题号、题干bbox、所属栏、是否跨栏），提升裁取精度。
2. 收集更多批改样本，标注红叉/红勾，训练本地小模型（如基于 HOG+SVM 或轻量 CNN）替代规则分类。
3. 引入 ECC 图像配准或多尺度匹配，提升弯曲书页的对齐精度。
4. 增加多页照片支持与跨页题目拼接。
5. 对阴影/光照做更鲁棒的预处理（如 LAB 色彩空间白平衡）。
6. 增加自动题号识别（轻量本地 OCR 或模板匹配）以校验候选题目。

## 13. 实际运行命令

```bash
# 安装依赖
pip install -r requirements.txt

# 端到端运行
python -m src.pipeline

# 单元测试
python -m unittest tests.test_basic
```

运行环境：
- Python 3.13.13
- Windows-11-10.0.26200-SP0
- OpenCV 5.0.0, PyMuPDF PyMuPDF 1.28.0: Python bindings for the MuPDF 1.29.0 library., NumPy 2.4.4, Pillow 11.3.0

## 14. 测试结果

- 总体状态：**success**
- 耗时：2.45s
- 原始素材保持不变：True
- 运行日志：

```
[任务2] PDF 67 页，照片 1279x1707
[输出] 01_original_photo.jpg
[任务4] 矫正方法: 四角透视矫正 (cv2.findContours + approxPolyDP + warpPerspective), success=True
[任务3] 渲染 8 个候选页 @ 200DPI
[输出] 05_feature_matches.jpg
[任务5] best_page=65, confidence=1.0, success=True
[任务5] 匹配页文字层为乱码(CJK字符数=0)，印证了不能依赖PDF文字层，特征匹配为正确路线
[输出] 04_pdf_candidate_page.jpg
[任务6] 红色标记数: 6; HSV=low=[(0, 70, 50)-(12, 255, 255)] + high=[(168, 70, 50)-(180, 255, 255)]
[任务7] cross=2, check=4, unknown=0
   - cross conf=0.722 bbox=(504, 1424, 186, 202) | cross_score=0.65 > check_score=0.25
   - cross conf=0.526 bbox=(983, 1350, 209, 194) | cross_score=0.50 > check_score=0.45
   - check conf=0.85 bbox=(580, 1121, 218, 204) | check_score=0.85 > cross_score=0.15
   - check conf=0.545 bbox=(979, 696, 263, 216) | check_score=0.60 > cross_score=0.50
   - check conf=1.0 bbox=(571, 575, 302, 140) | check_score=1.00 > cross_score=0.00
   - check conf=1.0 bbox=(531, 223, 260, 131) | check_score=1.00 > cross_score=0.00
[输出] 06_alignment_overlay.jpg, 09_marks_on_clean_pdf.jpg
[任务8] reprojection_error=1.992851974805897, align_conf=0.917, success=True
[输出] candidate_question_01.png bbox=(0, 1490, 858, 792) cross_col=False
[输出] candidate_question_02.png bbox=(834, 95, 859, 658) cross_col=True
[输出] candidate_question_03.png bbox=(834, 1387, 859, 792) cross_col=False
[任务9] 候选题目数: 3
[输出] result.json
```

### 输出文件清单

| 文件 | 说明 |
|---|---|
| 01_original_photo.jpg | 原始照片副本 |
| 02_detected_page_boundary.jpg | 检测到的书页边界 |
| 03_rectified_page.jpg | 透视矫正后的页面 |
| 04_pdf_candidate_page.jpg | 匹配到的 PDF 候选页 |
| 05_feature_matches.jpg | 特征匹配连线图 |
| 06_alignment_overlay.jpg | 配准叠加图 |
| 07_red_mask.png | 红色笔迹掩膜 |
| 08_detected_marks.jpg | 标记分类可视化 |
| 09_marks_on_clean_pdf.jpg | 映射到干净 PDF 的标记 |
| candidate_question_01.png | 候选原题 |
| candidate_question_02.png | 候选原题 |
| candidate_question_03.png | 候选原题 |
| result.json | 结构化结果 |
| technical_validation_report.md | 本报告 |
