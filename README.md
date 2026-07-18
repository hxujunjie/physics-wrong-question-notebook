# 物理练习册错题自动整理系统

教师端：**v1.6.1（多服务商智能识别）**

教师选择干净练习册 PDF + 学生作业照片目录 → 在线 AI 识别红笔批改 → 人工只复核异常项 → 从干净 PDF 裁题导出个人错题集。

## 老师请从这里下载（推荐）

**无需安装 Python。** 下载交付包 → 解压 → 双击即可使用：

- **发布页：** https://github.com/hxujunjie/physics-wrong-question-notebook/releases/tag/v1.6.1  
- **直接下载：** [physics-wrong-book-teacher-v1.6.1.zip](https://github.com/hxujunjie/physics-wrong-question-notebook/releases/download/v1.6.1/physics-wrong-book-teacher-v1.6.1.zip)

### 三步上手

1. 下载并解压 `physics-wrong-book-teacher-v1.6.1.zip`
2. 进入文件夹，双击 `启动教师端.bat` 或 `physics-wrong-book-teacher.exe`
3. 在浏览器中：粘贴 API Key → 选择干净练习册 PDF 与学生照片目录 → 识别 → 复核 → 生成错题集

需要自备：Windows 10/11、网络、练习册 PDF、学生作业照片、AI 服务商 API Key。

> 本仓库代码区为**源码**。练习册 PDF、真实学生作业、API 密钥不会提交到 GitHub；可运行安装包放在 **Releases**，不塞进源码树。

## 功能概览

- 本地浏览器教师端（默认仅 `127.0.0.1`）
- 多服务商：智谱 / 通义 / OpenAI·GPT / Google Gemini / xAI Grok / 豆包 / DeepSeek / 自定义
- API Key 本机加密存储（Windows DPAPI）
- 干净 PDF 索引缓存、照片识别断点续跑
- 教师复核 unknown / 低置信后导出错题 PDF

## 环境要求

- **Python 3.11–3.13**（开发常用 3.13）
- Windows 10/11（密钥加密依赖 Windows DPAPI）
- 依赖见 `requirements.txt` / `requirements-browser.lock.txt`

## 快速开始

```bat
首次安装依赖.bat
一键启动教师端.bat
```

或：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run_web_teacher.py
```

启动后在浏览器中配置 API Key，选择**你自己的**干净练习册 PDF 与学生照片目录即可。

更完整的使用说明见：

- `浏览器版使用说明.txt`
- `docs/RELEASE_v1.6.0.md`
- `docs/PROJECT_STRUCTURE.md`

## 测试

```bash
python -m unittest discover -s tests -q
```

## 打包（可选）

```bat
.venv\Scripts\pyinstaller.exe --noconfirm build_teacher.spec
```

产物在 `dist/`。正式交付包通常复制到本地 `release/` 目录；**二进制包体积大，不纳入本仓库**，如需分发请使用 [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github)。

## 仓库里有什么 / 没有什么

| 包含 | 不包含（本地可保留） |
|------|----------------------|
| `src/` `web/` `tests/` 源码 | `feishu_homework_by_student/` 真实学生作业 |
| 启动脚本、`requirements*.txt` | `release/` `dist/` 打包 exe |
| `docs/` 说明与流程图 | 练习册 PDF、批改样例图 |
| 浏览器使用说明 | `.venv/`、`_runtime/`、API 密钥 |

## 隐私与安全

- **不要**把真实学生姓名目录、作业原图、API Key 推送到公开仓库。
- 识别时会将压缩后的 PDF 页与学生照片发送到你所选的 AI 服务商；原始文件不会作为长期云端素材上传（详见使用说明中的安全边界）。
- 会话令牌仅用于本机教师端接口校验。

## 文档

- `docs/PROJECT_STRUCTURE.md` — 目录与模块
- `docs/RELEASE_v1.6.0.md` — v1.6.0 交付说明
- `docs/teacher_flow_interactive.html` — 教师端流程
- `docs/项目历程_错题本.html` — 项目历程

## 许可

若未另行声明，代码仅供学习与个人/校内使用；练习册等第三方教材版权归原权利人所有。
