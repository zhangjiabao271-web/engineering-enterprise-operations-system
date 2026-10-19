# 旧 Tk 界面退役

从 2026-10-19 起只维护 `web_workbench/static/` 一套界面。浏览器与 WebView2 独立窗口共用 Python 服务和本机 SQLite 账本。

## 启动与保留范围

- `run_workbench.vbs`：可录入桌面窗口，启动前保存完整备份。
- `run_desktop.vbs`、`run_web.bat`：同一 Web 工作台的只读入口，不是旧应用。
- `run_web_demo.bat`：全新合成演示，不读取正式资料。
- 旧 `main.py`、`run.bat`、`pages/`、`ui/`、专用界面测试和失效脚本已移除。业务服务、历史编号迁移、真实收支与旧账功能继续保留，不因源码退役而删除账本或附件。

## Excel 与依赖

`services/excel_transfer_service.py` 负责模板、每个非空行的校验、解析与工作簿生成，不再创建假控件或替换弹窗。隔离任务保留已有的局部连接适配及整批事务，预览和取消不写正式库；重复记录、缺少缓存的公式、歧义项目及过期预览会明确拒绝。项目创建使用显式共享连接，后续失败整笔回滚。

依赖移除 ttkbootstrap 并去重，Pillow 改为显式依赖，继续支持图片附件与 PDF 页面处理；不依赖旧界面包间接安装，不要求 Tcl/Tk。现有环境不自动卸载包。

## 验证

- `tests/test_excel_transfer_service.py`：整批回滚、精确金额、供应商税率、重复与过期拒绝及安全导出。
- `tests/test_tk_retirement.py`：阻断旧界面依赖、导出四类工作簿，并检查旧路径与依赖已移除。
- `scripts/accept_headless_desktop.py --output qa/全新目录`：在未安装 ttkbootstrap 的环境中，使用合成账本实测 WebView2 启动、进入导入导出页、关闭和端口释放；需安装桌面依赖及 WebView2。
- `scripts/accept_web_write_matrix.mjs`：当前可见页面的取消、保存、重复提交、落库、附件与进项 PDF 验收。可用 `SUPPLY_CHAIN_TEST_PYTHON` 指定隔离环境解释器；只连接配套合成服务。
- 完整回归、备份恢复与浏览器验收按 `WORKFLOW.md` 执行；历史测试结果不代替当前验收，未启用的外部模型也不视为已验证。

## 恢复旧版

源码可从退役前的 Git 提交检出到独立目录。不要覆盖当前工作区或直接用旧程序打开已升级的正式库；先保存当前源码、在线备份账本与附件，再在隔离副本核对版本兼容性。源码恢复不是账本恢复，不能据此覆盖或删除任何业务资料。

公开仓库不包含维护者本机的源码恢复包、测试环境、账本或验收截图。
