#!/usr/bin/env python3
"""Offline one-to-many / many-to-one LUT comparison with a scrolling gallery.

Generate one independent MKL/Oklab LUT per source/reference pair, plus shared
LUTs fitted from the entire set. All saved PNGs use reloaded .cube files.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import color_match_lut as c
import gallery_review as gallery

FUSION = ("pooled", "mean-cov", "bures", "robust-bures", "pairwise")


def run(content, style, output, methods=FUSION, content_weights=None, style_weights=None,
        size=65, max_pixels=0, overwrite=False):
    started = time.perf_counter()
    content = [Path(p).resolve() for p in content]
    style = [Path(p).resolve() for p in style]
    if not content or not style or (len(content) != 1 and len(style) != 1):
        raise ValueError("Provide one content image and multiple references, or multiple content images and one reference")
    if size not in (17, 33, 65) or max_pixels < 0:
        raise ValueError("size must be 17, 33 or 65; max-pixels must be nonnegative")
    methods = list(dict.fromkeys(methods))
    if not methods or any(method not in FUSION for method in methods):
        raise ValueError("Select at least one valid shared-LUT aggregation method")
    source_weights = c.weights(content_weights, len(content))
    target_weights = c.weights(style_weights, len(style))
    output = c.output_folder(output, overwrite)
    content_stats = c.collect_stats(content)
    style_stats = c.collect_stats(style)
    many_style = len(content) == 1
    mode = "one-to-many" if many_style else "many-to-one"
    independent_reports = []
    inputs = style if many_style else content
    for index, path in enumerate(inputs):
        si, ti = (0, index) if many_style else (index, 0)
        print(json.dumps({"pair": index + 1, "of": len(inputs), "status": "started"}), flush=True)
        independent_reports.append(c.run_fit(
            [content[si]], [style[ti]], output / "independent" / f"pair_{index + 1:03d}",
            methods=["bures"], size=size, apply_paths=[content[si]], max_pixels=max_pixels,
            overwrite=overwrite, prepared=([content_stats[si]], [style_stats[ti]])))
    shared = c.run_fit(content, style, output / "shared", methods=methods,
                       content_weights=source_weights, style_weights=target_weights,
                       size=size, apply_paths=content, max_pixels=max_pixels,
                       overwrite=overwrite, prepared=(content_stats, style_stats))

    assets = gallery.Assets(output, c)
    anchor_path = content[0] if many_style else style[0]
    anchor = assets.image(anchor_path, "固定内容图" if many_style else "固定风格参考")
    rows = []
    for index, path in enumerate(inputs):
        pair = independent_reports[index]["methods"]["bures"]
        row = {"id": f"row_{index + 1:03d}", "label": f'{"参考" if many_style else "内容"} {index + 1:02d}',
               "input": assets.image(path, f'{"风格参考" if many_style else "内容原图"} {index + 1:02d}'),
               "independent": assets.result(pair, label="逐张独立 MKL / Oklab")}
        if not many_style:
            row["shared"] = {method: assets.result(shared["methods"][method], index, c.LABELS[method])
                             for method in methods}
        rows.append(row)
    group = {"id": "one_many_comparison", "title": f'{len(content)} 张内容 × {len(style)} 张参考',
             "mode": mode, "anchor": anchor, "rows": rows,
             "note": f'每张{"参考" if many_style else "内容"}都有自己的独立追色 LUT。'
                     + ("下方另列全部参考共同拟合的共享滤镜。" if many_style else "共享方法一列的所有图片使用同一个 LUT。")}
    if many_style:
        group["shared"] = {"results": {method: assets.result(shared["methods"][method], 0, c.LABELS[method])
                                         for method in methods}}
    data = {"methods": methods, "labels": {method: c.LABELS[method] for method in methods}, "groups": [group]}
    review = gallery.write_gallery(data, output)
    reports = independent_reports + [shared]
    renders = [r for report in reports for info in report["methods"].values() for r in info["renders"]]
    summary = {"mode": mode, "content_images": len(content), "style_images": len(style),
               "comparison_rows": len(inputs), "independent_luts": len(independent_reports),
               "shared_luts": len(methods), "lut_files": len(independent_reports) + len(methods),
               "rendered_pngs": len(renders), "review": review,
               "all_png_lut_checks_passed": all(r["png_lut_verification_max_error_8bit"] == 0 for r in renders),
               "remote_image_analysis": False, "remote_image_payloads": False,
               "seconds": round(time.perf_counter() - started, 3)}
    configuration = {"summary": summary, "size": size, "max_pixels": max_pixels,
                     "methods": methods, "content": [str(p) for p in content], "style": [str(p) for p in style],
                     "content_weights": source_weights.tolist(), "style_weights": target_weights.tolist(),
                     "weight_semantics": "Weights affect shared-set fitting only; independent pairs each use their two images at full weight",
                     "independent_reports": [str(output / "independent" / f"pair_{i + 1:03d}" / "run.json")
                                             for i in range(len(inputs))],
                     "shared_report": str(output / "shared" / "run.json")}
    c.json_write(output / "comparison.json", configuration)
    c.json_write(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", nargs="+", required=True)
    parser.add_argument("--style", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--methods", nargs="+", choices=(*FUSION, "all"), default=list(FUSION))
    parser.add_argument("--content-weights", type=float, nargs="+")
    parser.add_argument("--style-weights", type=float, nargs="+")
    parser.add_argument("--size", type=int, choices=[17, 33, 65], default=65)
    parser.add_argument("--max-pixels", type=int, default=0,
                        help="0 preserves the original oriented resolution; otherwise bound output pixels")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args(argv)
    if args.max_pixels < 0:
        parser.error("max-pixels must be nonnegative")
    content = c.expand_paths(args.content, args.recursive)
    style = c.expand_paths(args.style, args.recursive)
    methods = FUSION if "all" in args.methods else args.methods
    run(content, style, args.output, methods, args.content_weights, args.style_weights,
        args.size, args.max_pixels, args.overwrite)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, FileExistsError, ArithmeticError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
