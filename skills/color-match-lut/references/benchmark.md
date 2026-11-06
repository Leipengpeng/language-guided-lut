# 双图追色方法对照

`compare_benchmark.py` 在同一张内容图和参考图上分别拟合所选方法。文字调色、单图追色和多图共享 LUT 的入口见 [Skill 说明](../SKILL.md)。

| 参数 | 拟合方式 | 相对于后端目录的文件 |
| --- | --- | --- |
| `mkl_oklab` | Oklab 高斯最优传输 | 随 Skill 提供 |
| `reinhard_lab` | CIE Lab D65 均值与标准差匹配 | 随 Skill 提供 |
| `histogram_lab` | CIE Lab 逐通道分位数匹配 | 随 Skill 提供 |
| `colorfm` | ColorFM-L 双图预测全局映射并采样 | `vendor/ColorFM/models/colorfm_l.py`，`models/ColorFM_L.pth` |
| `modflows` | ModFlows B6 双向流预测并采样 | `vendor/modflows/src/encoder.py`，`vendor/modflows/src/neural_ode.py`，`models/ModFlows_B6_751001.pt` |
| `dlut` | D-LUT 按参考图训练 EnergyScore 并以 Langevin 采样 | `vendor/D-LUT/codebase/models/toy_models.py`，`vendor/D-LUT/codebase/runner.py` |
| `reference_match` | Oklab 亮度及分区色度匹配，再将 Rec.709 gamma 2.4 映射烘焙到 sRGB | `vendor/reference-match/core/lutcore/global_match.py` |

## 本地运行

从仓库根目录运行。基础依赖是 NumPy、Pillow 和 SciPy。使用 Python 3.11 或更高版本也可运行后面需要本地权重的可选后端。

```sh
python -m pip install -r skills/color-match-lut/scripts/requirements.txt
python skills/color-match-lut/scripts/compare_benchmark.py --content input.jpg --reference reference.jpg --output outputs/compare --methods mkl_oklab reinhard_lab histogram_lab
```

`compare_benchmark.py --methods all` 执行六个主方法。`histogram_lab` 需要显式选择。`color_match_lut.py fit --methods all` 则对照 [共享 LUT 方法](algorithms.md)。

默认输出 65³、编码 sRGB、全范围 `[0,1]` 的 `.cube`。拟合视图最长边默认不超过 1024 像素，PNG 保留转正后原图的尺寸。每个方法目录包含 `look.cube`、从该 LUT 重载后渲染的 `result.png`、预览和 `model.json`。打开 `review.html` 可查看对照。`run.json` 记录输入哈希、方法及运行结果。LUT 与原生映射的误差衡量烘焙精度，美学效果由人工审阅。

## 可选模型与上游实现

四个可选后端都需要 PyTorch。ModFlows 额外需要与 PyTorch 匹配的 torchvision 和 einops。按平台安装 PyTorch 后可选择 `--device auto|cpu|cuda`，Reference Match 使用 CPU。将所需的完整上游代码及权重放到同一个后端目录，再用 `--backend-root` 指向该目录。也可设置 `COLOR_MATCH_LUT_BACKEND_ROOT`。上游代码及权重分别遵循其原始条款。

### ColorFM-L

下面以仓库内的 `local-backends` 为后端目录。从仓库根目录执行。补丁仅替换单进程推理用的警告导入。可在 [补丁文件](../../../research/colorfm-warning-only.patch) 查看。

```sh
python -c "from pathlib import Path; Path('local-backends/vendor').mkdir(parents=True, exist_ok=True); Path('local-backends/models').mkdir(parents=True, exist_ok=True)"
git clone https://github.com/cszn/ColorFM.git local-backends/vendor/ColorFM
git -C local-backends/vendor/ColorFM checkout 64c84c228cb505425c18546b886d3c80906f5e27
git -C local-backends/vendor/ColorFM apply ../../../research/colorfm-warning-only.patch
```

将 `ColorFM_L.pth` 保存为 `local-backends/models/ColorFM_L.pth`。官方权重模型卡标注 CC BY-NC 4.0。[ColorFM 官方权重](https://huggingface.co/heyh97791/ColorFM)

```sh
python skills/color-match-lut/scripts/compare_benchmark.py --content input.jpg --reference reference.jpg --output outputs/colorfm --methods colorfm mkl_oklab --backend-root local-backends
```

文字工作流的 `finish --method colorfm` 和 `finish --method auto` 的彩色分支使用同一布局。`auto` 的近黑白分支只需要基础依赖。

### 其他可选后端

| 后端 | 仓库与版本 | 文件位置 |
| --- | --- | --- |
| ModFlows B6 | [maria-larchenko/modflows](https://github.com/maria-larchenko/modflows/tree/ca5da1a2742fb6a63e03b7a1245662f47d278b24) | 源码放在 `vendor/modflows`。从[上游权重仓库](https://huggingface.co/MariaLarchenko/modflows_color_encoder)获取 `modflows_color_encoder_B6_dim_8195_iter_751001.pt`，保存为 `models/ModFlows_B6_751001.pt`。 |
| D-LUT | [leemojiang/D-LUT](https://github.com/leemojiang/D-LUT/tree/01927996eb03ad21be7f9cdbeb7a4c6604e7431c) | 源码放在 `vendor/D-LUT`。按参考图训练，缓存写入 `models/D-LUT-reference-cache`。 |
| Reference Match | [xrz1411/reference-match](https://github.com/xrz1411/reference-match/tree/88ddf8fa7c6e6d424ec2b55e32c1310276a52500) | 源码放在 `vendor/reference-match`。 |

使用 `--methods all` 前先准备四个可选后端的全部资源，也可用 `--methods` 只运行已准备的方法。

## 可选的本地 CLIP 配对

CLIP 从两个图片清单中选出场景相近的图对，再用上面的命令拟合。两份 JSONL 清单每行含 `"status": "ok"`、图片绝对路径 `path` 和可选的 `sha256`。使用同一个模型和缓存目录，场景过滤会复用图像特征，结果写入 `pairs.json`。相似度用于选图，色彩效果仍由人工审阅。

安装 [OpenAI CLIP](https://github.com/openai/CLIP)，可将其克隆到本仓库的 `vendor/CLIP` 后执行 `python -m pip install ./vendor/CLIP`。将 [官方 ViT-B/32 权重](https://openaipublic.azureedge.net/clip/models/40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af/ViT-B-32.pt) 保存到本仓库的 `models/clip/ViT-B-32.pt`。两个脚本共用这份权重，显式传入本地文件可避免配对时自动下载。

```sh
python scripts/pair_local_clip.py --content-manifest contents.jsonl --reference-manifest references.jsonl --model models/clip/ViT-B-32.pt --embedding-cache outputs/clip-embeddings --output outputs/pairing --lower 0.70 --upper 0.90 --pairs 30
python scripts/clip_semantic_guard.py --input-dir outputs/pairing --embedding-cache outputs/clip-embeddings --mode auto --lower 0.70 --upper 0.90 --pairs 30
```

