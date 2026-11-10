#!/usr/bin/env python3
"""Compare the six historical colour-transfer methods on one image pair."""
from __future__ import annotations

import argparse
from importlib import import_module
from importlib.util import find_spec
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

import numpy as np

from color_match_lut import (
    EXTENSIONS, apply_cube, digest, fit_model, gaussian_stats, grid, json_write,
    mapping, output_folder, preview, read_cube, read_srgb, render_image,
    serializable, to_oklab, write_cube,
)

LABELS = {
    "colorfm": "ColorFM-L",
    "modflows": "ModFlows B6",
    "dlut": "D-LUT",
    "reference_match": "Reference Match",
    "mkl_oklab": "MKL / Oklab",
    "reinhard_lab": "Reinhard / Lab",
    "histogram_lab": "Histogram / Lab",
}
HISTORICAL_METHODS = tuple(method for method in LABELS if method != "histogram_lab")
REQUIRED_ASSETS = {
    "colorfm": ("vendor/ColorFM/models/colorfm_l.py", "models/ColorFM_L.pth"),
    "modflows": (
        "vendor/modflows/src/encoder.py", "vendor/modflows/src/neural_ode.py",
        "models/ModFlows_B6_751001.pt",
    ),
    "dlut": ("vendor/D-LUT/codebase/models/toy_models.py", "vendor/D-LUT/codebase/runner.py"),
    "reference_match": ("vendor/reference-match/core/lutcore/global_match.py",),
}
ROOT_ENV = "COLOR_MATCH_LUT_BACKEND_ROOT"


def configure_assets(methods: list[str], explicit: Path | None) -> Path | None:
    required = [path for method in methods for path in REQUIRED_ASSETS.get(method, ())]
    if not required:
        return None
    dependencies = ["torch"]
    if "modflows" in methods:
        dependencies.extend(("torchvision", "einops"))
    missing_packages = [name for name in dependencies if find_spec(name) is None]
    if missing_packages:
        raise RuntimeError(f"Missing Python packages: {', '.join(missing_packages)}. See references/benchmark.md")
    configured = explicit or os.environ.get(ROOT_ENV)
    if configured:
        root = Path(configured).expanduser().resolve()
        missing = [path for path in required if not (root / path).is_file()]
        if missing:
            raise FileNotFoundError(f"Backend root {root} lacks: {', '.join(missing)}")
    else:
        cwd = Path.cwd()
        root = next(
            (folder for folder in (cwd, *cwd.parents) if all((folder / path).is_file() for path in required)),
            None,
        )
        if root is None:
            raise FileNotFoundError(f"Specify --backend-root with these local assets: {', '.join(required)}")
    os.environ[ROOT_ENV] = str(root)
    return root


def mkl_fit(content: np.ndarray, reference: np.ndarray, size: int) -> dict:
    def stats(rgb):
        colors = rgb.reshape(-1, 3)
        return gaussian_stats(to_oklab(colors[::max(1, len(colors) // 80000)]))

    model = fit_model([stats(content)], [stats(reference)], method="bures")
    return {
        "values": mapping(grid(size), model),
        "native_output": mapping(content, model),
        "metadata": {
            "algorithm": "Gaussian optimal transport in Oklab",
            "model": model,
            "pretrained_model": False,
        },
    }


def fit_method(method: str, content: np.ndarray, reference: np.ndarray, size: int, device: str) -> dict:
    if method == "mkl_oklab":
        return mkl_fit(content, reference, size)
    if method in {"reinhard_lab", "histogram_lab"}:
        from benchmark_backends.statistical_backends import fit_lut
        return fit_lut(content, reference, size=size, method=method)
    backend = import_module(f"benchmark_backends.{method}_backend")
    options = {} if device == "auto" or method == "reference_match" else {"device": device}
    return backend.fit_lut(content, reference, size=size, **options)


def write_review_page(output: Path, content: Path, reference: Path, methods: dict) -> str:
    def link(path):
        path = Path(path).resolve()
        try:
            relative = Path(os.path.relpath(path, output)).as_posix()
        except ValueError:  # Windows paths on different volumes.
            return path.as_uri()
        return quote(relative, safe="/:")

    content_item = {"name": content.name, "url": link(content), "preview": "content_preview.jpg"}
    reference_item = {"name": reference.name, "url": link(reference), "preview": "reference_preview.jpg"}
    results = {
        method: {
            "name": info["label"], "url": link(info["render"]["png"]),
            "preview": link(info["render"]["preview"]), "cube": link(info["cube"]),
        }
        for method, info in methods.items()
    }
    page = {"group": "双图追色对照", "title": content.name, "content": content_item,
            "references": [reference_item], "results": results}
    data = json.dumps({"pages": [page], "methods": list(methods), "labels": LABELS}, ensure_ascii=False)
    template = Path(__file__).resolve().parents[1] / "assets" / "review.html"
    path = output / "review.html"
    path.write_text(template.read_text(encoding="utf-8").replace("__DATA__", data.replace("</", "<\\/")), encoding="utf-8")
    return str(path)


def compare(args, methods: list[str]) -> dict:
    paths = [p.expanduser().resolve() for p in (args.content, args.reference)]
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            raise ValueError(f"Provide one supported image file for each role: {path}")
    backend_root = configure_assets(methods, args.backend_root)
    content_path, reference_path = paths
    content, source_info = read_srgb(content_path, long_edge=args.fit_edge)
    reference, reference_info = read_srgb(reference_path, long_edge=args.fit_edge)
    output = output_folder(args.output)
    preview(content, output / "content_preview.jpg")
    preview(reference, output / "reference_preview.jpg")
    source_rgb = np.asarray(content, dtype=np.float32) / 255
    reference_rgb = np.asarray(reference, dtype=np.float32) / 255
    report = {
        "requested_methods": methods, "backend_root": str(backend_root) if backend_root else None,
        "content": {"path": str(content_path), "sha256": digest(content_path), **source_info},
        "reference": {"path": str(reference_path), "sha256": digest(reference_path), **reference_info},
        "fit_edge": args.fit_edge, "render_max_pixels": args.max_pixels,
        "lut_size": args.size, "lut_domain": "encoded sRGB, full range [0,1]",
        "remote_inference": False, "methods": {}, "failures": {},
    }
    for method in methods:
        started = time.perf_counter()
        try:
            fitted = fit_method(method, source_rgb, reference_rgb, args.size, args.device)
            values = fitted["values"]
            native = fitted["native_output"]
            if values.shape != (args.size, args.size, args.size, 3) or native.shape != source_rgb.shape:
                raise ValueError("Unexpected backend LUT or image dimensions")
            if not np.isfinite(native).all():
                raise ValueError("Backend output contains nonfinite RGB")
            folder = output / method
            folder.mkdir()
            cube_path = folder / "look.cube"
            write_cube(cube_path, values, f"{method}_{args.size}_sRGB")
            table = read_cube(cube_path)
            errors = (apply_cube(source_rgb, table) - np.clip(native, 0, 1)) * 255
            result = render_image(content_path, table, folder / "result.png", args.max_pixels)
            info = serializable({
                "label": LABELS[method], "cube": str(cube_path), "cube_sha256": digest(cube_path),
                "reload_max_abs_error": float(np.max(np.abs(table - np.clip(values, 0, 1)))),
                "lut_vs_native_fit_rmse_8bit": float(np.sqrt(np.mean(errors**2))),
                "lut_vs_native_fit_p95_abs_8bit": float(np.quantile(np.abs(errors), .95)),
                "backend": fitted["metadata"], "render": result,
                "seconds": round(time.perf_counter() - started, 3),
            })
            json_write(folder / "model.json", info)
            report["methods"][method] = info
            print(json.dumps({"method": method, "status": "ok", "seconds": info["seconds"]}), flush=True)
        except Exception as error:
            report["failures"][method] = f"{type(error).__name__}: {error}"
            print(json.dumps({"method": method, "status": "failed", "error": report["failures"][method]}),
                  file=sys.stderr, flush=True)
        json_write(output / "run.json", report)
    if report["methods"]:
        report["review"] = write_review_page(output, content_path, reference_path, report["methods"])
        json_write(output / "run.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", type=Path, required=True, help="Photo to adjust")
    parser.add_argument("--reference", "--style", type=Path, required=True, help="Photo whose colours are the target")
    parser.add_argument("--output", type=Path, required=True, help="New or empty results directory")
    parser.add_argument("--methods", nargs="+", choices=("all", *LABELS), default=["all"])
    parser.add_argument("--backend-root", type=Path, help=f"Local vendor and model root; may also set {ROOT_ENV}")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--size", type=int, choices=(17, 33, 65), default=65)
    parser.add_argument("--fit-edge", type=int, default=1024, help="Maximum long edge used for fitting")
    parser.add_argument("--max-pixels", type=int, default=0, help="Optional output pixel cap; 0 preserves resolution")
    args = parser.parse_args(argv)
    if args.fit_edge <= 0 or args.max_pixels < 0:
        parser.error("--fit-edge must be positive and --max-pixels cannot be negative")
    if "all" in args.methods and args.methods != ["all"]:
        parser.error("Choose all or list individual methods")
    methods = list(HISTORICAL_METHODS) if args.methods == ["all"] else list(dict.fromkeys(args.methods))
    try:
        report = compare(args, methods)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps({"output": str(args.output.resolve()), "completed": len(report["methods"]),
                      "failed": len(report["failures"])}), flush=True)
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
