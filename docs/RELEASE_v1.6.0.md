# 教师端 v1.6.0 交付说明

**版本：** 1.6.0（多服务商智能识别）  
**交付形态：** 未压缩文件夹，直接运行 exe，无需安装 Python  

## 目录

```text
release/v1.6.0_教师端_多服务商智能识别_未压缩交付/
├─ 交付说明.txt
└─ 教师端_v1.6.0/
   ├─ physics-wrong-book-teacher.exe   # 主程序（双击启动）
   ├─ _internal/                       # 依赖（勿删、勿改）
   ├─ 使用说明.txt
   ├─ 启动教师端.bat
   └─ 创建桌面快捷方式.bat
```

## 主要能力

- 本地浏览器教师端（仅 127.0.0.1）
- 多服务商：智谱 / 通义 / OpenAI·GPT / Google Gemini / xAI Grok / 豆包 / DeepSeek / 自定义
- 模型下拉框（免费/低价标记）
- API Key 本机 DPAPI 加密
- 干净 PDF 索引缓存、照片识别断点续跑
- 教师复核 unknown / 低置信后导出错题 PDF
- 首次运行自动创建桌面快捷方式「物理错题整理-教师端」

## 与历史版本关系

| 版本 | 说明 |
|---|---|
| 学生端 2.1.1 + 教师端 1.2.1 | 离线双端最终交付（无在线 AI） |
| 教师端 1.3.0 | 本地浏览器复核工作台 |
| 教师端 1.5.x | Grok 一体化 |
| **教师端 1.6.0** | **多服务商 + 模型下拉（本包）** |

## 构建命令（开发者）

```bat
.venv\Scripts\pyinstaller.exe --noconfirm build_teacher.spec
```

然后将 `dist\physics-wrong-book-teacher\` 复制到上述 release 目录并附带说明文件。
