# 项目目录约定

本项目按“源码、测试、素材、运行输出、交付物和历史产物分开”的方式管理。

```text
physics-wrong-book/
├─ src/                   # Python 业务代码
├─ tests/                 # 可重复执行的自动化测试
├─ web/                   # 教师端浏览器静态资源
├─ data/                  # 按功能管理的本地数据
├─ docs/                  # 说明、报告和项目文档
├─ output/                # 当前流程的验证输出
├─ release/               # 已命名、可交付的版本
├─ artifacts/             # 不进 Git 的历史构建和调试产物
│  ├─ build/             # PyInstaller 构建缓存
│  ├─ dist/              # 历次未正式交付的打包目录
│  ├─ test-runs/         # 手工/E2E 测试输出
│  └─ tmp/               # 历史临时输出
├─ run_web_teacher.py     # 教师端服务入口
├─ launch_teacher.pyw     # Windows 图形启动入口
├─ requirements.txt       # 开发依赖
└─ README.md              # 项目入口说明
```

## 放置规则

- 新业务逻辑放在 `src/`，对应测试放在 `tests/`。
- 浏览器界面资源放在 `web/`，不与 Python 源码混放。
- 只有当前必需的入口、配置、依赖清单和原始只读素材留在根目录。
- `build*`、`dist*`、`tmp`和散落的测试运行结果不当作源码，历史内容归档到 `artifacts/`。
- 需要交给使用者的版本放在 `release/`，不从 `artifacts/dist/` 直接交付。
- 原始 PDF 和原始照片继续保持只读引用，不改名、不移动。
