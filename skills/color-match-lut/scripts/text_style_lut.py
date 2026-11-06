#!/usr/bin/env python3
"""Prepare a small colour-guide edit, then bake its colours into a local LUT.

This script runs locally. Codex performs the image_gen edit between the
prepare and finish commands. ColorFM-L fitting uses local model weights.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image, ImageOps

import color_match_lut as c

FORMAT = "text-style-lut-job-v1"
DEFAULT_UPLOAD_EDGE = 1080
MAX_UPLOAD_EDGE = 4096
PROMPT_PROFILES = ("natural", "balanced", "strong")
FINISH_METHODS = ("mkl_oklab", "auto", "colorfm")

_PROFILE_GUIDANCE = {
    "natural": "Use restrained adjustments by default.",
    "balanced": "Use visible, moderate adjustments by default.",
    "strong": "Use a more pronounced grade by default.",
}

_GLOBAL_LUT_GUARDRAILS = (
    "Simulate one global 3D LUT across the entire image. "
    "Identical input RGB values must produce identical output RGB values anywhere in the frame, "
    "regardless of position or subject. Apply hue-based and tonal adjustments uniformly wherever "
    "their input colors occur, including gentle treatment of skin-like colors. "
    "Do not use masks, local adjustments, separate foreground and background grades, left-right color "
    "splits, mirrored color effects, or position-based gradients. Do not infer or simulate lighting. "
    "If a style asks for contrasting hues, derive them from existing colors or tonal ranges and keep "
    "them independent of image position. "
)
GRADE_GUARDRAILS = (
    "Photographic color-grading guardrails: Adjust only color and tonal curves. "
    f"{_GLOBAL_LUT_GUARDRAILS}"
    "Preserve the existing light sources, their count, type, position, direction and falloff, "
    "along with the spatial pattern of highlights, shadows and reflections. "
    "Do not add neon signs, glowing objects, colored light sources, new rim lights or localized "
    "red or blue light, or relight the scene. "
    "Do not add, remove or redraw objects, people or background elements. "
    "Preserve framing, camera viewpoint, objects, people, identity, facial features, skin texture, "
    "hair, clothing and background details. Keep exposure credible with detail in highlights and shadows. "
    "Avoid newly clipped highlights, crushed shadows, extreme saturation, and unnatural skin hues in "
    "color renders. Use global color relationships that can be represented by a 3D LUT. "
    "Return one full-frame photograph at the input aspect ratio."
)


def _style(text, style_id=None):
    """Optional exact catalogue match. Unknown natural-language text stays intact."""
    path = Path(__file__).with_name("style_catalog.json")
    if not path.exists():
        if style_id:
            raise ValueError("The requested style catalogue is unavailable")
        return None
    catalogue = json.loads(path.read_text(encoding="utf-8"))
    styles = catalogue.get("styles", [])
    if style_id:
        found = next((s for s in styles if s.get("id") == style_id), None)
        if found is None:
            raise ValueError(f"Unknown style-id: {style_id}")
        return found
    value = text.strip().casefold()
    matches = [s for s in styles if value in {str(x).strip().casefold() for x in
                                             [s.get("id", ""), s.get("name", ""), *s.get("aliases", [])]}]
    return matches[0] if len(matches) == 1 else None


def make_prompt(text, prompt_profile="natural"):
    """Pass the style request as written and constrain how the photo may be graded."""
    if prompt_profile not in PROMPT_PROFILES:
        raise ValueError(f"prompt-profile must be one of {', '.join(PROMPT_PROFILES)}")
    return (
        "Apply the request below to the supplied photograph. Keep style names as written "
        "and follow the user's explicit color directions and intensity within the guardrails. "
        f"Selected prompt profile: {prompt_profile}. {_PROFILE_GUIDANCE[prompt_profile]} "
        "The guardrails apply at every intensity.\n"
        f"<style_request>\n{text}\n</style_request>\n\n"
        f"{GRADE_GUARDRAILS}"
    )


def _as_dict(path, **extra):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": c.digest(path), "bytes": path.stat().st_size, **extra}


def _protected_output(path, inputs, overwrite=False, allow_upload_only=False):
    path = Path(path).resolve()
    # None of the fixed output filenames may coincide with a source file.
    for p in inputs:
        if Path(p).resolve() in {path / name for name in
                                ("upload.jpg", "analysis_source.png", "job.json", "prompt.txt", "report.json",
                                 "source_fit.png", "style_fit.png", "lut_on_upload.png")}:
            raise ValueError("Output would overwrite an input file")
    allowed = allow_upload_only and path.is_dir() and {p.name for p in path.iterdir()} <= {"upload.jpg"}
    return c.output_folder(path, overwrite=overwrite or allowed)


def prepare(content, style_text, output, edge=DEFAULT_UPLOAD_EDGE, quality=85, style_id=None,
            overwrite=False, reuse_upload=False, prompt_profile="natural", *, image_prompt_file=None):
    content = Path(content).expanduser().resolve()
    if not content.is_file():
        raise FileNotFoundError(content)
    style_text = str(style_text)
    if not style_text.strip():
        raise ValueError("style-text must not be empty")
    if prompt_profile not in PROMPT_PROFILES:
        raise ValueError(f"prompt-profile must be one of {', '.join(PROMPT_PROFILES)}")
    if not 64 <= edge <= MAX_UPLOAD_EDGE:
        raise ValueError(f"edge must be between 64 and {MAX_UPLOAD_EDGE} pixels")
    if not 1 <= quality <= 95:
        raise ValueError("quality must be between 1 and 95")
    if image_prompt_file is None:
        matched = _style(style_text, style_id)
        prompt = make_prompt(style_text, prompt_profile)
    else:
        if style_id or prompt_profile != "natural":
            raise ValueError("A reviewed Image prompt already includes its style and intensity guidance")
        image_prompt_file = Path(image_prompt_file).expanduser().resolve()
        prompt = image_prompt_file.read_text(encoding="utf-8-sig")
        if not prompt.strip():
            raise ValueError("The reviewed Image prompt file is empty")
        if GRADE_GUARDRAILS not in prompt:
            previous_guardrails = GRADE_GUARDRAILS.replace(_GLOBAL_LUT_GUARDRAILS, "", 1)
            if previous_guardrails in prompt:
                prompt = prompt.replace(previous_guardrails, GRADE_GUARDRAILS)
            else:
                prompt += "\n\n" + GRADE_GUARDRAILS
        matched = None
    source_hash = c.digest(content)
    image, info = c.read_srgb(content, long_edge=edge)
    if c.digest(content) != source_hash:
        raise ValueError("Content changed during preparation")
    candidate = io.BytesIO()
    image.save(candidate, format="JPEG", quality=quality, optimize=True, subsampling=0,
               icc_profile=c.srgb_profile(), exif=b"")
    encoded = candidate.getvalue()
    inputs = [content] if image_prompt_file is None else [content, image_prompt_file]
    output = _protected_output(output, inputs, overwrite, allow_upload_only=True)
    upload = output / "upload.jpg"
    reused = False
    encoding_matches = True
    if upload.exists():
        encoding_matches = upload.read_bytes() == encoded
        if not encoding_matches and not reuse_upload:
            raise ValueError("An existing upload.jpg differs from this encoding. It was NOT overwritten. "
                             "Use a new directory or --reuse-upload to explicitly keep that exact edit target")
        reused = True
    else:
        if reuse_upload:
            raise FileNotFoundError("--reuse-upload requires an existing upload.jpg")
        upload.write_bytes(encoded)
    # Decode the exact upload bytes, not a different uncompressed pre-JPEG view.
    with Image.open(upload) as uploaded:
        if uploaded.format != "JPEG" or max(uploaded.size) > edge:
            raise ValueError("Existing upload must be a JPEG no larger than the selected edge")
        if uploaded.getexif():
            raise ValueError("Upload JPEG must not contain EXIF; use a new prepared image")
        if not uploaded.info.get("icc_profile"):
            raise ValueError("Upload JPEG must contain its sRGB ICC profile")
        embedded = c.ImageCms.ImageCmsProfile(io.BytesIO(uploaded.info["icc_profile"]))
        if "srgb" not in c.ImageCms.getProfileDescription(embedded).casefold():
            raise ValueError("Upload JPEG must be encoded with an identifiable sRGB ICC profile")
        decoded = uploaded.convert("RGB")
        upload_size = list(decoded.size)
        expected_ratio = image.width / image.height
        if abs((decoded.width / decoded.height) / expected_ratio - 1) > .02:
            raise ValueError("Existing upload aspect ratio differs from the content preview")
        if min(decoded.size) < 1:
            raise ValueError("Invalid upload dimensions")
        analysis_path = output / "analysis_source.png"
        decoded.save(analysis_path, icc_profile=c.srgb_profile())
    # Exact source provenance remains clear even when the parent already submitted this JPEG.
    target = np.asarray(image.resize(tuple(upload_size), Image.Resampling.LANCZOS), dtype=np.float32)
    actual = np.asarray(decoded, dtype=np.float32)
    encoding_rmse = float(np.sqrt(np.mean((actual - target) ** 2)))
    prompt_path = output / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    original = _as_dict(content, oriented_size=info["source_size"])
    job = {
        "format": FORMAT, "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "script_sha256": c.digest(Path(__file__)), "style_text": style_text,
        "prompt_profile": prompt_profile if image_prompt_file is None else None,
        "prompt_origin": "legacy_profile" if image_prompt_file is None else "reviewed_image_prompt",
        "style_catalog_match": matched, "content": original,
        "upload": _as_dict(upload, size=upload_size, edge_limit=edge,
                           jpeg_quality=quality if encoding_matches else None,
                           requested_jpeg_quality=quality, requested_encoding_bytes_match=encoding_matches,
                           reused_existing_bytes=reused, decoded_rmse_vs_uncompressed_preview_8bit=encoding_rmse),
        "analysis_source": _as_dict(analysis_path, size=upload_size,
                                    relationship="lossless PNG of the exact uploaded JPEG decoded pixels"),
        "prompt_file": str(prompt_path.resolve()), "prompt_sha256": c.digest(prompt_path),
        "preparation": info, "upload_bytes_fraction_of_original": upload.stat().st_size / original["bytes"],
        "upload_pixels_fraction_of_original": (upload_size[0] * upload_size[1]) /
                                                (info["source_size"][0] * info["source_size"][1]),
        "image_edit": {"tool": "image_gen", "edit_target": str(upload.resolve()),
                       "transparent_background": False, "only_upload_this_image": True,
                       "full_resolution_original_must_stay_local": True},
        "network_actions_by_script": 0,
    }
    job_path = output / "job.json"
    c.json_write(job_path, job)
    print(json.dumps({"stage": "prepared", "job": str(job_path), "upload_size": upload_size,
                      "upload_bytes": job["upload"]["bytes"], "reused_upload": reused}), flush=True)
    return job


def _verified_input(record, name):
    path = Path(record["path"]).resolve()
    if not path.is_file() or c.digest(path) != record["sha256"]:
        raise ValueError(f"{name} changed or is missing; prepare a new job before editing/fitting")
    return path


def _fit_colorfm(source_fit, style_fit, output, apply_paths, size, max_pixels, backend_root, device):
    from compare_benchmark import fit_method

    source_rgb = np.asarray(source_fit, dtype=np.float32) / 255
    style_rgb = np.asarray(style_fit, dtype=np.float32) / 255
    fitted = fit_method("colorfm", source_rgb, style_rgb, size, device)
    folder = output / "colorfm"
    folder.mkdir(parents=True, exist_ok=True)
    cube_path = folder / "look.cube"
    c.write_cube(cube_path, fitted["values"], f"ColorFM-L_{size}_sRGB")
    loaded = c.read_cube(cube_path)
    renders = [
        c.render_image(path, loaded, folder / "renders" / f"{i:03d}_{path.stem}.png", max_pixels)
        for i, path in enumerate(apply_paths, 1)
    ]
    result = {"label": "ColorFM-L", "cube": str(cube_path), "cube_sha256": c.digest(cube_path),
              "backend": fitted["metadata"], "renders": renders}
    c.json_write(output / "run.json", c.serializable({
        "backend_root": str(backend_root), "lut_size": size,
        "remote_inference": False, "methods": {"colorfm": result},
    }))
    return result


def finish(job_path, styled, output=None, apply=False, size=65, max_pixels=0,
           strength=1., tone_strength=1., color_strength=1., overwrite=False, *,
           method="mkl_oklab", backend_root=None, device="auto"):
    job_path = Path(job_path).expanduser().resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    if job.get("format") != FORMAT:
        raise ValueError("Unsupported job format")
    if size not in (17, 33, 65) or max_pixels < 0:
        raise ValueError("size must be 17, 33 or 65 and max-pixels must be nonnegative")
    if method not in FINISH_METHODS:
        raise ValueError(f"method must be one of {', '.join(FINISH_METHODS)}")
    content = _verified_input(job["content"], "Original content")
    upload = _verified_input(job["upload"], "Upload JPEG")
    analysis = _verified_input(job["analysis_source"], "Analysis source")
    styled = Path(styled).expanduser().resolve()
    if not styled.is_file():
        raise FileNotFoundError(styled)
    # Validate the exact JPEG/PNG association before trusting it as an aligned source.
    upload_image, _ = c.read_srgb(upload)
    analysis_image, _ = c.read_srgb(analysis)
    if upload_image.size != analysis_image.size or not np.array_equal(np.asarray(upload_image), np.asarray(analysis_image)):
        raise ValueError("analysis_source.png does not contain the exact uploaded JPEG pixels")
    target_image, target_preparation = c.read_srgb(styled)
    classification = None
    selected_method = method
    if method == "auto":
        from monochrome import classify_reference

        classification = classify_reference(target_image)
        selected_method = "mkl_oklab" if classification["is_monochrome"] else "colorfm"
    force_neutral = classification is not None and classification["is_monochrome"]
    if selected_method == "colorfm":
        if (strength, tone_strength, color_strength) != (1., 1., 1.):
            raise ValueError("Strength controls are only available for MKL/Oklab")
        from compare_benchmark import configure_assets

        backend_root = configure_assets(["colorfm"], backend_root)
    with Image.open(styled) as target_file:
        original_format = target_file.format
    output = _protected_output(output or job_path.parent / "render", [content, upload, analysis, styled], overwrite)
    report_path = output / "report.json"
    if overwrite:
        report_path.unlink(missing_ok=True)
    suffix = styled.suffix.lower() or ".bin"
    archived = output / ("generated_style_original" + suffix)
    if styled != archived.resolve():
        shutil.copyfile(styled, archived)
    # Only shrink for fitting. No crop, no synthesis, no resolution invented for analysis.
    fitting_edge = min(max(upload_image.size), max(target_image.size))
    source_fit = ImageOps.contain(upload_image, (fitting_edge, fitting_edge), Image.Resampling.LANCZOS)
    style_fit = ImageOps.contain(target_image, (fitting_edge, fitting_edge), Image.Resampling.LANCZOS)
    source_fit_path, style_fit_path = output / "source_fit.png", output / "style_fit.png"
    source_fit.save(source_fit_path, icc_profile=c.srgb_profile())
    style_fit.save(style_fit_path, icc_profile=c.srgb_profile())
    apply_paths = [content] if apply else []
    if selected_method == "colorfm":
        result = _fit_colorfm(source_fit, style_fit, output / "fit", apply_paths, size,
                              max_pixels, backend_root, device)
        fit_engine = "ColorFM-L"
    else:
        fit = c.run_fit([source_fit_path], [style_fit_path], output / "fit", methods=["bures"],
                        size=size, fit_edge=fitting_edge, strength=strength, tone_strength=tone_strength,
                        color_strength=color_strength, apply_paths=apply_paths, max_pixels=max_pixels,
                        overwrite=overwrite, monochrome=force_neutral)
        result = fit["methods"]["bures"]
        fit_engine = "single-pair MKL/Oklab"
    cube_path = Path(result["cube"])
    loaded = c.read_cube(cube_path)
    proxy_rgb = np.asarray(source_fit, dtype=np.float32) / 255
    proxy_result = c.apply_cube(proxy_rgb, loaded)
    proxy_path = output / "lut_on_upload.png"
    Image.fromarray(np.rint(proxy_result * 255).astype(np.uint8)).save(proxy_path, icc_profile=c.srgb_profile())
    # A diagnostic, not a judgment about aesthetics or a guarantee that image_gen preserved geometry.
    guide_resized = style_fit.resize(source_fit.size, Image.Resampling.LANCZOS)
    guide_rgb = np.asarray(guide_resized, dtype=np.float32) / 255
    difference = (proxy_result - guide_rgb) * 255
    aspect_error = abs((target_image.width / target_image.height) /
                       (upload_image.width / upload_image.height) - 1)
    report = {
        "format": "text-style-lut-result-v1", "job": str(job_path), "style_text": job["style_text"],
        "style_catalog_match": job.get("style_catalog_match"),
        "prompt_origin": job.get("prompt_origin", "legacy_profile"),
        "prompt_sha256": job.get("prompt_sha256"),
        "content": job["content"], "upload": job["upload"], "analysis_source": job["analysis_source"],
        "styled_input": _as_dict(archived, size=list(target_image.size), original_format=original_format,
                                 copied_from=str(styled), preparation=target_preparation),
        "source_fit": _as_dict(source_fit_path, size=list(source_fit.size)),
        "style_fit": _as_dict(style_fit_path, size=list(style_fit.size)),
        "lut": _as_dict(cube_path, size=size, domain="encoded sRGB, full range [0,1]"),
        "lut_on_upload": _as_dict(proxy_path, size=list(source_fit.size)),
        "render": result["renders"][0] if result["renders"] else None,
        "fit_report": str(output / "fit/run.json"), "fit_engine": fit_engine,
        "routing": {"requested_method": method, "selected_method": selected_method,
                    "reference_classification": classification, "force_neutral_output": force_neutral},
        "generated_style_archived_in_output": True, "network_actions_by_script": 0,
        "diagnostics": {
            "generated_vs_upload_relative_aspect_error": float(aspect_error),
            "aspect_ratio_changed_more_than_two_percent": bool(aspect_error > .02),
            "lut_vs_generated_guide_rmse_8bit": float(np.sqrt(np.mean(difference ** 2))),
            "lut_vs_generated_guide_p95_abs_8bit": float(np.quantile(np.abs(difference), .95)),
            "interpretation": "Global-colour-fit approximation only; no geometry-preservation or aesthetic claim. "
                              "A global LUT cannot reproduce line art, changed objects, texture or spatially local edits.",
        },
        "transfer_files": {"uploaded_image_bytes": job["upload"]["bytes"],
                           "returned_image_file_bytes": archived.stat().st_size,
                           "note": "File bytes only; actual tool transport, latency and token costs are not measured here."},
    }
    c.json_write(report_path, report)
    print(json.dumps({"stage": "finished", "report": str(report_path), "method": selected_method,
                      "cube": str(cube_path), "rendered": apply,
                      "output_size": report["render"]["output_size"] if apply else None}), flush=True)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("prepare", help="Prepare the only image that may be sent to image_gen")
    a.add_argument("--content", type=Path, required=True)
    a.add_argument("--style-text", required=True)
    a.add_argument("--style-id")
    a.add_argument("--image-prompt-file", type=Path,
                   help="Use a reviewed UTF-8 Image prompt as prompt.txt without adding legacy guidance")
    a.add_argument("--prompt-profile", choices=PROMPT_PROFILES, default="natural",
                   help="Qualitative prompt guidance; natural is restrained, strong requires explicit selection")
    a.add_argument("--output", type=Path, required=True)
    a.add_argument("--edge", type=int, default=DEFAULT_UPLOAD_EDGE,
                   help="Maximum upload long edge in pixels. Smaller input photos are not upscaled.")
    a.add_argument("--quality", type=int, default=85)
    a.add_argument("--reuse-upload", action="store_true", help="Keep exact existing upload.jpg bytes already submitted for editing")
    a.add_argument("--overwrite", action="store_true")
    b = sub.add_parser("finish", help="Bake returned image colours and optionally render the original")
    b.add_argument("--job", type=Path, required=True)
    b.add_argument("--styled", type=Path, required=True)
    b.add_argument("--output", type=Path)
    b.add_argument("--apply", action="store_true")
    b.add_argument("--method", choices=FINISH_METHODS, default="mkl_oklab",
                   help="Auto routes color to ColorFM-L and monochrome to neutral-output MKL/Oklab")
    b.add_argument("--backend-root", type=Path, help="Local vendor and model root for ColorFM-L")
    b.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto",
                   help="Inference device for ColorFM-L")
    b.add_argument("--size", type=int, choices=[17, 33, 65], default=65)
    b.add_argument("--max-pixels", type=int, default=0)
    b.add_argument("--strength", type=float, default=1.)
    b.add_argument("--tone-strength", type=float, default=1.)
    b.add_argument("--color-strength", type=float, default=1.)
    b.add_argument("--overwrite", action="store_true")
    args = p.parse_args(argv)
    if args.command == "prepare":
        reviewed = {"image_prompt_file": args.image_prompt_file} if args.image_prompt_file else {}
        prepare(args.content, args.style_text, args.output, args.edge, args.quality,
                args.style_id, args.overwrite, args.reuse_upload, args.prompt_profile, **reviewed)
    else:
        finish(args.job, args.styled, args.output, args.apply, args.size, args.max_pixels,
               args.strength, args.tone_strength, args.color_strength, args.overwrite,
               method=args.method, backend_root=args.backend_root, device=args.device)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, FileExistsError, ArithmeticError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
