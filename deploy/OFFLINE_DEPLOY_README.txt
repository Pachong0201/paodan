# 断网环境部署说明（离线运行爆料邮箱筛选引擎）

本引擎在无网络、无 LLM 的单机上可完整运行（规则模式全离线）。

## 需要拷贝的文件（2 个 zip）

1. `deps_offline.zip`   —— 全部 Python 依赖（41MB，已含 PyMuPDF/pdf/docx/excel/繁转简）
2. `project_offline.zip` —— 项目本体（app 代码 + config 规则包 + tests 样本）

## 安装步骤（在断网机上执行一次）

要求：先装好 **Python 3.12**（与打包机同版本；3.10-3.12 大概率兼容，3.13 需重打包）。

```bat
:: 1) 解压项目
解压 project_offline.zip 到 D:\paodan

:: 2) 解压依赖到 Python 的 site-packages
::    找到断网机的 Python 安装位置，例如：
::    C:\Users\<你>\AppData\Local\Programs\Python\Python312\Lib\site-packages
::    把 deps_offline.zip 解压到该 site-packages 目录内（注意不要多套一层文件夹）

:: 3) 验证安装
python -c "import fitz, docx, openpyxl, PIL, yaml, opencc; print('deps ok')"
:: 输出 deps ok 即成功
```

若断网机 Python 版本不同或想更干净，可改用另一方案：把有网机器的
`Lib\site-packages` 整体复制覆盖断网机（同版本最稳）。

## 运行（此后无需任何网络）

```bat
:: 放 .eml 到 data\inbox 后：
python -m app.main --input data\inbox\           :: 批量筛选
python -m app.main --input data\inbox\ --no-llm  :: 纯规则模式(推荐,行为完全确定)
python -m app.main --file 单封邮件.eml
python -m app.main --input data\inbox\ --min-priority B   :: 只看 B 级以上
python -m app.main --selfcheck                    :: 规则包自检
```

## 断网环境的注意事项

- 不要配置 .env 的 LLM_API_KEY/LLM_MODE=api（那是联网模式）；保持 template/不配置即可。
- 图片/扫描 PDF 的 OCR 需另装 tesseract（可选）；不装则图片附件标记 skipped，正文与
  文本层 PDF 不受影响。
- 测试：`python -m pytest tests -q`（38 项，全离线）。
- 已知限制：本离线包依赖为 Windows + Python3.12 构建，换 Linux/mac 需在有网环境重打。

## 输出

- data\reports\priority_queue.csv   —— 记者队列（S/A/B/C/D + 摘要 + 核查清单）
- data\reports\screening_results.jsonl —— 完整明细
- logs\app.log、data\news_screening.db
