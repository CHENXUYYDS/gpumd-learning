# Materials Project 元素检索与 CIF 数据

保存日期：2026-11-03（Asia/Shanghai）。下载工具版本：1.0.2。

本目录保存通用下载脚本，以及一次 Al / Mg / Si / Cu 全部子体系查询的结果。

## 文件

- `download_cif.py`：主程序。
- `requirements.txt`：重建 Python 环境所需依赖。
- `start.bat` / `start.sh`：Windows / Linux 启动入口。
- [使用说明](使用说明.md)：安装、输入元素、运行与恢复方式。
- `MP_CIF_Al-Cu-Mg-Si_subsystems/`：846 个 CIF，以及材料清单、断点记录和下载报告。

## 本次数据快照

- 来源：[Materials Project](https://materialsproject.org/)，通过官方 mp-api 获取 summary 结构，并使用 pymatgen 导出为 CIF。
- 查询：Al、Cu、Mg、Si 的15个非空元素子体系，排除其他元素。
- 默认过滤：未弃用条目，包含稳定和不稳定结构。
- 查询时间：2026-11-03T08:20:12+00:00（北京时间16:20:12）。
- 报告：匹配846条，下载846条，待下载0条，失败0条，14个子体系有匹配结果。
- 每个材料ID保存一份结构；这不是所有历史计算任务或所有可能结构的全集。
- 打包前按 download_state.json 的 SHA256 核对了全部846个CIF。
- CIF没有完整的DFT受力参考标签，不能直接作为已标注NEP训练集。

保留了原材料ID、来源链接、清单及报告。引用和后续分享数据时应保留来源说明，并查阅 Materials Project 相应数据的使用与引用要求。这里不替第三方数据重新授予许可。

## 在另一台电脑运行

先根据[使用说明](使用说明.md)建立新的`.venv`并安装requirements.txt，再使用start.bat或start.sh。Python环境不随仓库上传。

API Key在运行时输入，或由运行机器的MP_API_KEY环境变量提供。不要把Key写入代码、README或仓库中的配置文件。

## 加入已有GitHub仓库

将本文件夹整体复制到已有仓库的本地根目录，保留现有仓库README和其他内容。确认GitHub Desktop显示新增文件，再Commit并Push origin。本文件夹中的.gitignore只管理本目录，不覆盖已有仓库的忽略规则。
