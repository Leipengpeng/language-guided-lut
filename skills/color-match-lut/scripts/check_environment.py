"""Check local grading dependencies without installing packages or using the network."""

import argparse
import contextlib
import importlib
import io
import json
from pathlib import Path
import sys


PACKAGES = (
    ("numpy", "numpy"),
    ("Pillow", "PIL.Image"),
    ("Pillow", "PIL.ImageCms"),
    ("scipy", "scipy.ndimage"),
    ("scipy", "scipy.optimize"),
    ("pypng", "png"),
)
MIRRORS = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.ustc.edu.cn/pypi/simple",
)


def check(guide=False, xmp=False):
    scripts = Path(__file__).resolve().parent
    checks = []
    supported = sys.version_info >= (3, 11)
    checks.append({"item": "Python 3.11+", "ok": supported})
    packages = list(PACKAGES)
    if guide:
        packages.append(("scikit-image", "skimage.color"))
    if supported:
        for package, module in packages:
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    importlib.import_module(module)
            except Exception as error:
                checks.append({"item": module, "package": package, "ok": False,
                               "detail": f"{type(error).__name__}: {error}"})
            else:
                checks.append({"item": module, "package": package, "ok": True})

    required = ["requirements.txt", "color_match_lut.py", "text_style_lut.py", "png16.py"]
    if guide:
        required.extend(["reference_guide.py", "../assets/film_color_corpus.json",
                         "../assets/reference_guide_baseline.json"])
    if xmp:
        required.extend(["lut_to_xmp.py", "xmp_preset.py", "adobe_response.py",
                         "../assets/rawmakase/color_mixer_chart.bin",
                         "../assets/rawmakase/black_white_chart.bin"])
    for relative in required:
        checks.append({"item": relative, "ok": (scripts / relative).is_file(),
                       "kind": "skill_file"})

    missing = sorted({item["package"] for item in checks
                      if not item["ok"] and "package" in item})
    failed = [item for item in checks if not item["ok"]]
    commands = []
    if supported and missing:
        for mirror in MIRRORS:
            commands.append([sys.executable, "-m", "pip", "install", "-r",
                             str(scripts / "requirements.txt"), "--index-url", mirror,
                             "--timeout", "20", "--retries", "1"])
    if failed:
        message = "环境检查未通过。请先完成列出的环境配置，再重新运行检查和调色。"
    else:
        message = "本地基础环境检查通过。请另行核对宿主工具，以及所选可选后端的依赖和权重。"
    return {"status": "blocked" if failed else "ready", "message": message,
            "python": sys.executable, "python_version": sys.version.split()[0],
            "checks": checks, "missing_packages": missing,
            "suggested_install_commands": commands,
            "host_tools": "Agent 另行核对文件读取、搜索、图像编辑和多图输入是否可用。"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guide", action="store_true", help="Check calibrated same-photo guide dependencies")
    parser.add_argument("--xmp", action="store_true", help="Check editable XMP export resources")
    args = parser.parse_args()
    report = check(guide=args.guide, xmp=args.xmp)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
