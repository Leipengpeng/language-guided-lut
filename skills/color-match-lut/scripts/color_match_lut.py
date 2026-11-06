#!/usr/bin/env python3
"""Offline MKL/Oklab colour transfer, multi-image aggregation and .cube export.

Only NumPy, Pillow and SciPy are required. No model, network or image upload.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps
from scipy.ndimage import map_coordinates

VERSION = "1.0.0"
REG = 1e-6
METHODS = ("pooled", "mean-cov", "bures", "robust-bures", "pairwise", "anchor")
LABELS = {"pooled": "混合统计", "mean-cov": "统计量平均", "bures": "Wasserstein 重心",
          "robust-bures": "稳健加权重心", "pairwise": "映射平均", "anchor": "单图 MKL 基线"}
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
Image.MAX_IMAGE_PIXELS = 180_000_000


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_write(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def weights(values, count):
    w = np.ones(count, dtype=np.float64) if values is None else np.asarray(values, dtype=np.float64)
    if w.shape != (count,) or not np.isfinite(w).all() or np.any(w < 0) or w.sum() <= 0:
        raise ValueError("Weights must be finite, nonnegative, match the image count, and have positive sum")
    return w / w.sum()


def expand_paths(items, recursive=False):
    result = []
    for item in items:
        p = Path(item).expanduser().resolve()
        candidates = sorted(p.rglob("*") if recursive else p.iterdir()) if p.is_dir() else [p]
        for candidate in candidates:
            if candidate.is_dir():
                continue
            if candidate.suffix.lower() not in EXTENSIONS:
                if not p.is_dir():
                    raise ValueError(f"Unsupported image format: {candidate}. Convert RAW/HDR/HEIC to SDR sRGB first")
                continue
            if not candidate.is_file():
                raise FileNotFoundError(candidate)
            if candidate in result:
                raise ValueError(f"Duplicate image path: {candidate}; use explicit weights instead")
            result.append(candidate)
    if not result:
        raise ValueError("No supported images found")
    return result


def read_srgb(path, long_edge=0, max_pixels=0):
    """EXIF orientation and ICC normalization; full resolution unless explicitly bounded."""
    with Image.open(path) as original:
        bits = original.tag_v2.get(258, (8,)) if hasattr(original, "tag_v2") else (8,)
        bits = (bits,) if isinstance(bits, int) else bits
        if original.mode in {"I", "F", "I;16", "I;16B", "I;16L"} or max(bits) > 8:
            raise ValueError(f"16-bit/HDR input requires explicit conversion to 8-bit SDR sRGB: {path}")
        profile = original.info.get("icc_profile")
        image = ImageOps.exif_transpose(original)
        source_size = list(image.size)
        scale = 1.0
        if long_edge:
            scale = min(scale, long_edge / max(image.size))
        if max_pixels:
            scale = min(scale, math.sqrt(max_pixels / (image.width * image.height)))
        if scale < 1:
            image = image.resize(tuple(max(1, round(x * scale)) for x in image.size), Image.Resampling.LANCZOS)
        alpha = image.getchannel("A") if "A" in image.getbands() else None
        # Keep CMYK/LAB mode until LittleCMS has interpreted its profile.
        if profile:
            source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            image = ImageCms.profileToProfile(image.convert("RGB") if alpha is not None else image,
                                             source, ImageCms.createProfile("sRGB"), outputMode="RGB")
        else:
            image = image.convert("RGB")
        if alpha is not None:
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=alpha)
            image = background
        return image.copy(), {"source_size": source_size, "working_size": list(image.size),
                              "icc": "converted_to_sRGB" if profile else "no_profile_assumed_sRGB",
                              "alpha": "composited_on_white" if alpha is not None else "none"}


def srgb_profile():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def to_oklab(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    linear = np.where(rgb <= .04045, rgb / 12.92, ((np.maximum(rgb, 0) + .055) / 1.055) ** 2.4)
    m1 = np.array([[.4122214708, .5363325363, .0514459929], [.2119034982, .6806995451, .1073969566],
                   [.0883024619, .2817188376, .6299787005]])
    m2 = np.array([[.2104542553, .7936177850, -.0040720468], [1.9779984951, -2.4285922050, .4505937099],
                   [.0259040371, .7827717662, -.8086757660]])
    return np.cbrt(linear @ m1.T) @ m2.T


def from_oklab(lab):
    m1 = np.array([[1, .3963377774, .2158037573], [1, -.1055613458, -.0638541728],
                   [1, -.0894841775, -1.2914855480]])
    m2 = np.array([[4.0767416621, -3.3077115913, .2309699292], [-1.2684380046, 2.6097574011, -.3413193965],
                   [-.0041960863, -.7034186147, 1.7076147010]])
    linear = ((np.asarray(lab) @ m1.T) ** 3) @ m2.T
    return np.where(linear <= .0031308, 12.92 * linear,
                    1.055 * np.maximum(linear, 0) ** (1 / 2.4) - .055)


def sym(m):
    return (m + m.T) * .5


def psd_power(m, exponent):
    eigenvalues, eigenvectors = np.linalg.eigh(sym(np.asarray(m, dtype=np.float64)))
    # Do not floor the positive square root of a nested covariance at 1e-6.
    floor = max(float(eigenvalues.max()) * 1e-12, 1e-15) if exponent < 0 else 0.0
    return sym((eigenvectors * np.maximum(eigenvalues, floor) ** exponent) @ eigenvectors.T)


def gaussian_stats(lab):
    lab = np.asarray(lab, dtype=np.float64).reshape(-1, 3)
    if not len(lab) or not np.isfinite(lab).all():
        raise ValueError("Empty or non-finite colour samples")
    covariance = np.cov(lab.T) if len(lab) > 1 else np.zeros((3, 3))
    return {"mean": lab.mean(0), "covariance": sym(covariance) + REG * np.eye(3)}


def collect_stats(paths, fit_edge=1024, sample_target=80000):
    records = []
    for path in paths:
        image, info = read_srgb(path, long_edge=fit_edge)
        flat = np.asarray(image, dtype=np.float32).reshape(-1, 3) / 255
        # Preserve the original single-pair benchmark's deterministic stride.
        samples = flat[::max(1, len(flat) // sample_target)]
        record = gaussian_stats(to_oklab(samples))
        record.update(path=str(path), sha256=digest(path), samples=len(samples), preparation=info)
        records.append(record)
    return records


def gaussian_distance_squared(a, b):
    root = psd_power(a["covariance"], .5)
    trace = np.trace(a["covariance"] + b["covariance"] - 2 * psd_power(root @ b["covariance"] @ root, .5))
    return float(max(0.0, np.sum((a["mean"] - b["mean"]) ** 2) + trace))


def covariance_barycenter(covs, w, tolerance=1e-9, max_iterations=256):
    """Gaussian W2 covariance barycenter, Alvarez-Esteban et al. fixed point."""
    active = [(c, float(p)) for c, p in zip(covs, w) if p > 0]
    if len(active) == 1:
        return active[0][0].copy(), {"iterations": 0, "relative_residual": 0.0}
    current = sum(p * c for c, p in active)
    for iteration in range(1, max_iterations + 1):
        root, inv = psd_power(current, .5), psd_power(current, -.5)
        mean_roots = sum(p * psd_power(root @ c @ root, .5) for c, p in active)
        following = sym(inv @ mean_roots @ mean_roots @ inv)
        delta = np.linalg.norm(following - current) / max(np.linalg.norm(current), 1e-15)
        current = following
        if delta < tolerance:
            root = psd_power(current, .5)
            rhs = sum(p * psd_power(root @ c @ root, .5) for c, p in active)
            residual = float(np.linalg.norm(current - rhs) / max(np.linalg.norm(current), 1e-15))
            if residual < 1e-7:
                return current, {"iterations": iteration, "relative_residual": residual}
    raise ArithmeticError("Gaussian barycenter did not converge; inspect the image set or use mean-cov")


def robust_weights(records, base):
    """A documented engineering heuristic: W2 medoid + soft distance clipping."""
    active = np.flatnonzero(base > 0)
    if len(active) < 3:
        return base.copy(), {"rule": "fewer_than_three_active_images_no_reweighting"}
    distances = np.zeros((len(records), len(records)))
    for pos, i in enumerate(active):
        for j in active[pos + 1:]:
            distances[i, j] = distances[j, i] = math.sqrt(gaussian_distance_squared(records[i], records[j]))
    medoid = min(active, key=lambda i: float(distances[i] @ base))
    d = distances[medoid]
    def weighted_median(x):
        order = np.argsort(x[active], kind="stable")
        selected = active[order]
        return float(x[selected[np.searchsorted(np.cumsum(base[selected]), .5)]])
    median = weighted_median(d)
    mad = weighted_median(np.abs(d - median))
    cutoff = max(median + 2.5 * 1.4826 * mad, 1e-4)
    effective = weights(base * np.minimum(1, cutoff / np.maximum(d, 1e-12)), len(base))
    return effective, {"rule": "weighted_W2_medoid_soft_clip", "medoid_index": int(medoid),
                       "distances": d.tolist(), "cutoff": cutoff}


def aggregate(records, w, method):
    effective = w.copy()
    meta = {}
    if method == "robust-bures":
        effective, meta = robust_weights(records, w)
    mean = sum(p * r["mean"] for p, r in zip(effective, records))
    if method == "pooled":
        covariance = sum(p * (r["covariance"] + np.outer(r["mean"] - mean, r["mean"] - mean))
                         for p, r in zip(effective, records))
    elif method == "mean-cov":
        covariance = sum(p * r["covariance"] for p, r in zip(effective, records))
    elif method in {"bures", "robust-bures"}:
        covariance, convergence = covariance_barycenter([r["covariance"] for r in records], effective)
        meta["barycenter"] = convergence
    else:
        raise ValueError(method)
    return {"mean": mean, "covariance": sym(covariance)}, {**meta, "effective_weights": effective.tolist()}


def transport(source, target):
    cs, ct = source["covariance"], target["covariance"]
    root, inv = psd_power(cs, .5), psd_power(cs, -.5)
    matrix = sym(inv @ psd_power(root @ ct @ root, .5) @ inv)
    error = float(np.linalg.norm(matrix @ cs @ matrix.T - ct) / max(np.linalg.norm(ct), 1e-15))
    if error > 1e-5:
        raise ArithmeticError(f"MKL covariance invariant failed: {error}")
    return matrix, target["mean"] - matrix @ source["mean"], error


def fit_model(content, style, method="bures", content_weights=None, style_weights=None,
              strength=1.0, tone_strength=1.0, color_strength=1.0):
    if method not in METHODS:
        raise ValueError(method)
    for value in (strength, tone_strength, color_strength):
        if not np.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Strength values must be in [0,1]")
    ws, wt = weights(content_weights, len(content)), weights(style_weights, len(style))
    details = {}
    if method in {"pairwise", "anchor"}:
        matrix, bias, max_error = np.zeros((3, 3)), np.zeros(3), 0.0
        source_indices = np.flatnonzero(ws > 0)
        target_indices = np.flatnonzero(wt > 0)
        if method == "anchor":
            source_indices, target_indices = source_indices[:1], target_indices[:1]
        for i in source_indices:
            for j in target_indices:
                a, b, error = transport(content[i], style[j])
                coefficient = 1.0 if method == "anchor" else ws[i] * wt[j]
                matrix += coefficient * a
                bias += coefficient * b
                max_error = max(error, max_error)
        details.update(component_covariance_error_max=max_error,
                       pair_count=len(source_indices) * len(target_indices),
                       content_effective_weights=ws.tolist(), style_effective_weights=wt.tolist())
        if method == "anchor":
            details["anchor_indices"] = [int(source_indices[0]), int(target_indices[0])]
    else:
        source, source_info = aggregate(content, ws, method)
        target, target_info = aggregate(style, wt, method)
        matrix, bias, error = transport(source, target)
        details.update(source=source, target=target, content_aggregation=source_info,
                       style_aggregation=target_info, covariance_transport_relative_error=error)
    return {"method": method, "matrix": matrix, "bias": bias, "strength": strength,
            "tone_strength": tone_strength, "color_strength": color_strength, "details": details}


def mapping(rgb, model, clip=True):
    lab = to_oklab(rgb)
    delta = lab @ model["matrix"].T + model["bias"] - lab
    delta *= np.array([model["tone_strength"], model["color_strength"], model["color_strength"]]) * model["strength"]
    output = from_oklab(lab + delta)
    return np.clip(output, 0, 1).astype(np.float32) if clip else output


def _neutral_output(rgb):
    # Preserve luma of the graded sRGB values and encode it identically in R, G, and B.
    luma = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
    return np.repeat(luma[..., None], 3, axis=-1)


def grid(size):
    axis = np.linspace(0, 1, size, dtype=np.float32)
    return np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)


def write_cube(path, values, title="MKL Oklab"):
    n = values.shape[0]
    if values.shape != (n, n, n, 3) or not np.isfinite(values).all():
        raise ValueError("Invalid LUT values")
    temp = Path(path).with_suffix(".cube.tmp")
    with temp.open("w", encoding="ascii", newline="\n") as f:
        title = title.encode("ascii", errors="replace").decode().replace('"', "").replace("\n", " ")
        f.write(f'TITLE "{title}"\n# Encoded sRGB input/output, full range. Not a Log/HDR conversion.\n')
        f.write(f"LUT_3D_SIZE {n}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n")
        np.savetxt(f, np.clip(values, 0, 1).transpose(2, 1, 0, 3).reshape(-1, 3), fmt="%.9f")
    temp.replace(path)


def read_cube(path):
    size, rows = None, []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        key = fields[0]
        if key == "TITLE":
            continue
        if key == "LUT_3D_SIZE":
            size = int(fields[1])
            if not 2 <= size <= 129:
                raise ValueError("Unsupported LUT size")
        elif key in {"DOMAIN_MIN", "DOMAIN_MAX"}:
            expected = 0.0 if key == "DOMAIN_MIN" else 1.0
            if len(fields) != 4 or any(float(v) != expected for v in fields[1:]):
                raise ValueError("Only full-range [0,1] LUT domains are supported")
        elif key.startswith("LUT_"):
            raise ValueError("Only a standalone 3D .cube is supported")
        else:
            if len(fields) != 3:
                raise ValueError("Malformed .cube row")
            rows.append([float(v) for v in fields])
    if size is None or len(rows) != size ** 3:
        raise ValueError("LUT row count does not match LUT_3D_SIZE")
    table = np.asarray(rows, dtype=np.float32).reshape(size, size, size, 3).transpose(2, 1, 0, 3)
    if not np.isfinite(table).all() or table.min() < 0 or table.max() > 1:
        raise ValueError("LUT must contain finite SDR values in [0,1]")
    return table


def apply_cube(rgb, table):
    rgb = np.asarray(rgb)
    coords = (np.clip(rgb.reshape(-1, 3), 0, 1) * (len(table) - 1)).T
    result = np.stack([map_coordinates(table[..., c], coords, order=1, mode="nearest", prefilter=False)
                       for c in range(3)], axis=-1)
    return np.clip(result.reshape(rgb.shape), 0, 1).astype(np.float32)


def preview(image, path):
    image = ImageOps.contain(image, (1600, 1200), Image.Resampling.LANCZOS)
    image.save(path, quality=92, icc_profile=srgb_profile())


def render_image(path, cube, output, max_pixels=0):
    image, preparation = read_srgb(path, max_pixels=max_pixels)
    output = Path(output)
    if output.resolve() == Path(path).resolve():
        raise ValueError("Output would overwrite an input image")
    output.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20261102)
    yy = rng.integers(0, image.height, 2048)
    xx = rng.integers(0, image.width, 2048)
    sampled = np.array([image.getpixel((int(x), int(y))) for y, x in zip(yy, xx)], dtype=np.float32) / 255
    expected = np.rint(apply_cube(sampled, cube) * 255).astype(np.uint8)
    result = Image.new("RGB", image.size)
    for y in range(0, image.height, 128):
        band = np.asarray(image.crop((0, y, image.width, min(y + 128, image.height))), dtype=np.float32) / 255
        encoded = np.rint(apply_cube(band, cube) * 255).astype(np.uint8)
        result.paste(Image.fromarray(encoded), (0, y))
    result.save(output, icc_profile=srgb_profile(), compress_level=3)
    with Image.open(output) as saved:
        saved.load()
        actual = np.array([saved.getpixel((int(x), int(y))) for y, x in zip(yy, xx)], dtype=np.uint8)
    error = int(np.abs(actual.astype(int) - expected.astype(int)).max())
    if error:
        raise ArithmeticError("Saved PNG pixels do not match the reloaded LUT")
    preview_path = output.with_name(output.stem + "_preview.jpg")
    preview(result, preview_path)
    return {"input": str(Path(path).resolve()), "png": str(output.resolve()),
            "preview": str(preview_path.resolve()), "preparation": preparation,
            "output_size": list(result.size), "verification_samples": len(xx),
            "png_lut_verification_max_error_8bit": error}


def serializable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def output_folder(path, overwrite=False):
    path = Path(path).resolve()
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise FileExistsError(f"Output folder is not empty: {path}; choose a new folder or pass --overwrite")
    path.mkdir(parents=True, exist_ok=True)
    return path


def review_pages(report, output, group="追色结果"):
    """Build local thumbnail links only; never return image bytes to the agent."""
    import os
    from urllib.parse import quote
    output = Path(output).resolve()
    assets = output / "review_assets"
    assets.mkdir(exist_ok=True)
    def link(path):
        path = Path(path).resolve()
        try:
            relative = Path(os.path.relpath(path, output)).as_posix()
        except ValueError:  # Windows input and output can be on different volumes.
            return path.as_uri()
        return quote(relative, safe="/:")
    def item(path):
        path = Path(path)
        thumbnail = assets / (hashlib.sha256(str(path).encode()).hexdigest()[:16] + ".jpg")
        if not thumbnail.exists():
            image, _ = read_srgb(path, long_edge=1600)
            preview(image, thumbnail)
        return {"name": path.name, "preview": link(thumbnail), "url": link(path)}
    references = [item(r["path"]) for r in report["style"]]
    first = next(iter(report["methods"].values()))
    pages = []
    for i, rendered in enumerate(first["renders"]):
        content = item(rendered["input"])
        results = {}
        for method, info in report["methods"].items():
            result = info["renders"][i]
            results[method] = {"preview": link(result["preview"]), "url": link(result["png"]),
                               "cube": link(info["cube"]), "name": Path(result["input"]).name}
        pages.append({"group": group, "title": f'{len(report["content"])} → {len(report["style"])} · {content["name"]}',
                      "content": content, "references": references, "results": results})
    return pages


def write_review(pages, output, methods):
    template = Path(__file__).resolve().parents[1] / "assets" / "review.html"
    payload = json.dumps({"pages": pages, "methods": list(methods), "labels": LABELS}, ensure_ascii=False).replace("</", "<\\/")
    path = Path(output) / "review.html"
    path.write_text(template.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    return str(path.resolve())


def run_fit(content_paths, style_paths, output, methods=("bures",), content_weights=None, style_weights=None,
            size=65, fit_edge=1024, strength=1., tone_strength=1., color_strength=1.,
            apply_paths=(), max_pixels=0, overwrite=False, prepared=None, *, monochrome=False):
    started = time.perf_counter()
    output = output_folder(output, overwrite)
    if prepared is None:
        source, target = collect_stats(content_paths, fit_edge), collect_stats(style_paths, fit_edge)
    else:
        source, target = prepared
    methods = list(dict.fromkeys(methods))
    if not methods or any(m not in METHODS for m in methods):
        raise ValueError("Select at least one valid method")
    ws, wt = weights(content_weights, len(source)), weights(style_weights, len(target))
    report = {"version": VERSION, "script_sha256": digest(Path(__file__)), "remote_inference": False,
              "lut_domain": "encoded sRGB input/output, full range [0,1]", "lut_size": size,
              "covariance_regularizer": REG, "fit_long_edge": fit_edge,
              "sample_target": 80000, "sampling": "legacy deterministic stride; fewer than 2x target samples",
              "content": source, "style": target, "content_weights": ws, "style_weights": wt,
              "render_max_pixels": max_pixels, "methods": {}}
    lattice = grid(size)
    random_colors = np.random.default_rng(20261102).random((4096, 3), dtype=np.float32)
    render_cache = {}
    for method in methods:
        t0 = time.perf_counter()
        folder = output / method
        folder.mkdir(exist_ok=True)
        model = fit_model(source, target, method, ws, wt, strength, tone_strength, color_strength)
        raw = mapping(lattice, model, clip=False)
        values = np.clip(raw, 0, 1).astype(np.float32)
        path = folder / "look.cube"
        title = f"MKL_Oklab_{method}_{size}"
        expected = mapping(random_colors, model)
        if monochrome:
            values = _neutral_output(values)
            expected = _neutral_output(expected)
            title += "_monochrome"
        write_cube(path, values, title)
        loaded = read_cube(path)  # All previews are rendered from the actual saved LUT.
        table_hash = hashlib.sha256(loaded.tobytes()).hexdigest()
        delta = (apply_cube(random_colors, loaded) - expected) * 255
        info = {"label": LABELS[method], "cube": str(path.resolve()), "cube_sha256": digest(path),
                "model": model, "reload_max_abs_error": float(np.abs(loaded - values).max()),
                "random_rgb_lut_vs_analytic_rmse_8bit": float(np.sqrt(np.mean(delta ** 2))),
                "random_rgb_lut_vs_analytic_p95_abs_8bit": float(np.quantile(np.abs(delta), .95)),
                "grid_out_of_gamut_channel_fraction": float(np.mean((raw < 0) | (raw > 1))), "renders": []}
        if monochrome:
            info["output_projection"] = "neutral RGB from BT.709 luma of encoded sRGB"
        for index, image_path in enumerate(apply_paths):
            name = f"{index + 1:03d}_{Path(image_path).stem}.png"
            destination = folder / "renders" / name
            cache_key = (str(image_path), table_hash)
            if cache_key in render_cache:
                import os
                import shutil
                previous = render_cache[cache_key]
                destination.parent.mkdir(parents=True, exist_ok=True)
                preview_destination = destination.with_name(destination.stem + "_preview.jpg")
                for old, new in [(previous["png"], destination), (previous["preview"], preview_destination)]:
                    if new.exists():
                        new.unlink()
                    try:
                        os.link(old, new)
                    except OSError:
                        shutil.copyfile(old, new)
                rendered = {**previous, "png": str(destination.resolve()), "preview": str(preview_destination.resolve()),
                            "reused_identical_lut_render": True}
            else:
                rendered = render_image(image_path, loaded, destination, max_pixels)
                render_cache[cache_key] = rendered
            info["renders"].append(rendered)
        info["seconds"] = round(time.perf_counter() - t0, 3)
        report["methods"][method] = info
        json_write(folder / "model.json", serializable(info))
        print(json.dumps({"method": method, "status": "ok", "renders": len(apply_paths),
                          "seconds": info["seconds"]}), flush=True)
    if apply_paths:
        report["review"] = write_review(review_pages(report, output), output, methods)
    report["seconds"] = round(time.perf_counter() - started, 3)
    report = serializable(report)
    json_write(output / "run.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("fit", help="Fit one shared LUT per method from image sets")
    fit.add_argument("--content", nargs="+", required=True)
    fit.add_argument("--style", nargs="+", required=True)
    fit.add_argument("--content-weights", type=float, nargs="+")
    fit.add_argument("--style-weights", type=float, nargs="+")
    fit.add_argument("--methods", nargs="+", choices=(*METHODS, "all"), default=["bures"])
    fit.add_argument("--size", type=int, choices=[17, 33, 65], default=65)
    fit.add_argument("--fit-edge", type=int, default=1024)
    fit.add_argument("--strength", type=float, default=1.)
    fit.add_argument("--tone-strength", type=float, default=1.)
    fit.add_argument("--color-strength", type=float, default=1.)
    fit.add_argument("--apply", action="store_true", help="Apply exported/reloaded LUTs to all content images")
    fit.add_argument("--apply-to", nargs="+", help="Apply to these other images, instead of the fitting content")
    apply = commands.add_parser("apply", help="Apply an existing sRGB .cube without fitting again")
    apply.add_argument("--lut", type=Path, required=True)
    apply.add_argument("--content", nargs="+", required=True)
    for command in (fit, apply):
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--max-pixels", type=int, default=0, help="0 preserves the original oriented resolution")
        command.add_argument("--recursive", action="store_true")
        command.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.max_pixels < 0 or (args.command == "fit" and args.fit_edge < 16):
        parser.error("max-pixels must be nonnegative and fit-edge must be >=16")
    content = expand_paths(args.content, args.recursive)
    if args.command == "fit":
        style = expand_paths(args.style, args.recursive)
        apply_paths = expand_paths(args.apply_to, args.recursive) if args.apply_to else content if args.apply else []
        methods = METHODS if "all" in args.methods else args.methods
        run_fit(content, style, args.output, methods, args.content_weights, args.style_weights,
                args.size, args.fit_edge, args.strength, args.tone_strength, args.color_strength,
                apply_paths, args.max_pixels, args.overwrite)
    else:
        cube = read_cube(args.lut)
        folder = output_folder(args.output, args.overwrite)
        report = {"cube": str(args.lut.resolve()), "cube_sha256": digest(args.lut), "remote_inference": False,
                  "renders": [render_image(p, cube, folder / f"{i + 1:03d}_{p.stem}.png", args.max_pixels)
                              for i, p in enumerate(content)]}
        json_write(folder / "apply.json", report)
        print(json.dumps({"status": "ok", "renders": len(content), "output": str(folder)}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, FileExistsError, ArithmeticError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
