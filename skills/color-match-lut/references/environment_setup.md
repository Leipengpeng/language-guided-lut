# 环境配置与失败处理

## 首次检查

先确认宿主能够读取用户原片、运行本地 Python，以及调用图像编辑工具。命名风格还需可用的图片或网页搜索入口。多图引导需要宿主支持同一请求传入两张图片。搜索和图像编辑是宿主能力，Python 检查器不能代替这两项核查。

寻找现有 Python 3.11 或更高版本，优先复用完整环境。在 Windows 可检查 `py --list`、`python --version` 和已有虚拟环境。下列命令中的 `<skill>` 是 `SKILL.md` 所在目录。选择解释器后，安装、检查和调色都使用同一个解释器。

```sh
python "<skill>/scripts/check_environment.py" --guide --xmp
```

核心追色需要 NumPy、Pillow、SciPy 和 PyPNG。`png` 对应的发行包为 `pypng`。按目标色差标定引导图还需要 `scikit-image`。XMP 的 RAWmakase 响应表已经随 Skill 提供。

检查器输出 JSON。`ready` 的退出码为 0，`blocked` 的退出码为 2。遇到缺失或导入失败时，Agent 停止本次调色，并按下表报告。用户请求完成环境配置后，再在隔离环境中执行安装和复查。

| 问题 | 告诉用户的内容 | 配置后如何继续 |
| --- | --- | --- |
| 未安装 Python 或版本不足 | 需要 Python 3.11+，给出当前检测结果和系统对应的安装方式 | 重新选择解释器并运行检查器 |
| 缺少模块或二进制依赖导入失败 | 当前解释器、模块、对应包名和检查器记录的错误 | 安装声明依赖，或改用已有的完整环境，再次检查 |
| 缺少随包脚本或 XMP 响应表 | 列出缺失文件，说明需要重新安装完整 Skill | 重新解压完整安装包，再次检查 |
| 宿主不能运行脚本 | 需要启用宿主的代码执行能力或换用支持本地 Python 的工作模式 | 能运行检查器后继续 |
| 图像编辑工具未启用、无权限或服务不可达 | 说明需要可编辑上传图片的工具，以及宿主实际返回的失败信息 | 工具恢复后复用准备文件继续 |
| 仅多图编辑不可用 | 说明会使用用户原意及保护约束的单图路线 | 保留实际输入数量和采用原因 |
| 图片搜索不可达 | 列出实际失败入口和使用的替代入口 | 按搜索回退规则继续或说明未完成风格研究 |
| 用户指定的 ColorFM 等后端缺代码、权重或依赖 | 列出所缺项和[对应安装位置](benchmark.md)，说明该方法尚未运行 | 资源齐全再运行；改用 MKL/Oklab 时先说明并按用户选择执行 |

仅涉及自动搜索入口时优先复用宿主内置工具。核心 MKL/Oklab 与 XMP 导出无需安装浏览器驱动、CLIP、PyTorch 或下载 ColorFM 权重。只有用户选定的可选方法需要这些资源时才检查它们。

## 国内镜像与离线安装

用户环境无法访问境外 PyPI、默认源超时，或用户要求国内源时，优先用清华镜像，以中科大镜像备用。使用单次命令的 `--index-url`，保留系统现有配置。下列命令在用户要求配置环境后执行。

```sh
python -m pip install -r "<skill>/scripts/requirements.txt" --index-url https://pypi.tuna.tsinghua.edu.cn/simple --timeout 20 --retries 1
```

清华源不可达时替换为 `https://mirrors.ustc.edu.cn/pypi/simple`。同一失败入口尝试一次，备用入口仍失败就停止，报告超时、证书错误、权限错误或无适配安装包等实际原因。`No matching distribution` 需检查 Python 版本和平台。缺少 pip 时，先检查 `python -m ensurepip --upgrade` 是否受该 Python 发行版支持。

国内镜像需要网络。所有网络均不可达时，使用为目标操作系统、处理器和 Python 版本准备的离线 wheel 目录。可在同配置的联网机器下载，再把目录带回目标机器。

```sh
python -m pip download -r "<skill>/scripts/requirements.txt" --dest wheelhouse --index-url https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install --no-index --find-links wheelhouse -r "<skill>/scripts/requirements.txt"
```

Python 镜像只提供 Python 依赖。搜索引擎、图像生成服务、GitHub 源码和模型权重分别核查可达性。源码或权重下载失败时，提供上游地址和预期文件位置，支持用户提供本地副本；安装镜像不代表这些服务已恢复。完全离线且没有本地图像编辑模型时，可做已有参考图的追色与现成 LUT 的套用。

镜像地址依据 [清华 PyPI 使用帮助](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)与[中科大 PyPI 使用帮助](https://mirrors.ustc.edu.cn/help/pypi.html)。

## 对用户的失败说明

用简短中文说明当前停在哪一步、哪些部分已经完成、确切缺失项和下一条可执行的配置操作。例如「尚未开始生成调色参考。当前 Python 缺少 PyPNG，`import png` 失败。请按上面的命令在这个解释器安装依赖，检查通过后可以继续。」保留原片、已准备的小图和提示词，环境恢复后复用已有准备结果。
