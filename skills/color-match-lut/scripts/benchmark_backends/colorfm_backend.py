"""Run the official ColorFM-L and bake its pair-conditioned RGB map to a 3D LUT.

The returned LUT has axis order [input R, input G, input B, output RGB].
Inputs/outputs are display-referred RGB floats in [0, 1], matching the official
application. The real image pair determines the kernels; the identity cube is
only evaluated with those frozen kernels, never fed back to the image encoder.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
import torch
from . import backend_root

ROOT = backend_root()
SOURCE = ROOT / "vendor" / "ColorFM"
CHECKPOINT = ROOT / "models" / "ColorFM_L.pth"


def _validate_rgb(image: np.ndarray, name: str) -> np.ndarray:
    image = np.asarray(image, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3 or min(image.shape[:2]) == 0:
        raise ValueError(f"{name} must be a nonempty HWC RGB array")
    if not np.isfinite(image).all() or image.min() < 0 or image.max() > 1:
        raise ValueError(f"{name} must contain finite RGB values in [0, 1]")
    return np.ascontiguousarray(image)


@lru_cache(maxsize=2)
def _load_model(device_name: str):
    if not SOURCE.is_dir() or not CHECKPOINT.is_file():
        raise FileNotFoundError("ColorFM source or models/ColorFM_L.pth is missing")
    if str(SOURCE) not in sys.path:
        sys.path.insert(0, str(SOURCE))
    from models.colorfm_l import ColorFM_L

    device = torch.device(device_name)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    model = ColorFM_L(cfg=None)
    state = torch.load(CHECKPOINT, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model.to(device=device, dtype=torch.float32).eval()
    return model, device


@lru_cache(maxsize=1)
def _provenance() -> dict:
    digest = hashlib.sha256()
    with CHECKPOINT.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        revision = None
    return {
        "repository": "https://github.com/cszn/ColorFM",
        "source_commit": revision,
        "checkpoint": str(CHECKPOINT),
        "checkpoint_url": "https://huggingface.co/heyh97791/ColorFM/resolve/main/ColorFM_L.pth",
        "checkpoint_bytes": CHECKPOINT.stat().st_size,
        "checkpoint_sha256": digest.hexdigest(),
        "code_license": "Apache-2.0",
        "checkpoint_license": "CC-BY-NC-4.0",
        "source_patches": ["Replace training-only rank-zero warning helper with Python warnings.warn; model math unchanged"],
    }


def _map_pixels(pixels: torch.Tensor, kernels: dict[str, torch.Tensor]) -> torch.Tensor:
    """Exact two-stage pointwise mapping from the official forward method."""
    outputs = []
    for points in pixels.split(262144, dim=1):
        time_zero = torch.zeros_like(points[..., :1])
        velocity = torch.cat((points, time_zero), dim=-1)
        for layer in range(1, 5):
            velocity = torch.bmm(velocity, kernels[f"kernel{layer}_content"])
            if layer < 4:
                velocity = velocity * torch.sigmoid(velocity)
        intermediate = points + velocity
        time_one = torch.ones_like(intermediate[..., :1])
        velocity = torch.cat((intermediate, time_one), dim=-1)
        for layer in range(1, 5):
            velocity = torch.bmm(velocity, kernels[f"kernel{layer}_style"])
            if layer < 4:
                velocity = velocity * torch.sigmoid(velocity)
        outputs.append(intermediate - velocity)
    return torch.cat(outputs, dim=1)


def fit_lut(
    content_rgb: np.ndarray,
    reference_rgb: np.ndarray,
    size: int = 33,
    *,
    device: str | None = None,
) -> dict:
    """Return values, official native_output, and reproducibility metadata.

    This uses the released trained model, not per-pair gradient fitting. Its
    global color map is then sampled into a conventional, clipped RGB 3D LUT.
    """
    if not isinstance(size, int) or size < 2 or size > 129:
        raise ValueError("LUT size must be an integer between 2 and 129")
    content_rgb = _validate_rgb(content_rgb, "content_rgb")
    reference_rgb = _validate_rgb(reference_rgb, "reference_rgb")
    device_name = device or ("cuda" if torch.cuda.is_available() else "cpu")
    start = time.perf_counter()
    model, target = _load_model(device_name)
    loaded = time.perf_counter()
    if target.type == "cuda":
        torch.cuda.synchronize(target)
        torch.cuda.reset_peak_memory_stats(target)

    def to_tensor(array):
        return torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).contiguous().to(target)

    with torch.inference_mode():
        native, _, _ = model(to_tensor(content_rgb), to_tensor(reference_rgb))
        kernels = {key: value.detach().clone() for key, value in model.get_params().items()}
        # Independently confirm the frozen map reproduces the real pair's
        # native output before sampling any cube. Subsample deterministically.
        points = to_tensor(content_rgb).flatten(2).transpose(1, 2)
        stride = max(1, points.shape[1] // 10000)
        reconstructed = _map_pixels(points[:, ::stride], kernels)
        native_points = native.flatten(2).transpose(1, 2)[:, ::stride]
        reconstruction_max = float((reconstructed - native_points).abs().max().item())
        if reconstruction_max > 2e-5:
            raise RuntimeError(f"Frozen ColorFM map differs from native output: {reconstruction_max}")

        axis = np.linspace(0.0, 1.0, size, dtype=np.float32)
        grid = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
        grid_tensor = torch.from_numpy(grid.reshape(1, -1, 3)).to(target)
        raw_values = _map_pixels(grid_tensor, kernels).cpu().numpy().reshape(size, size, size, 3)
        raw_native = native[0].permute(1, 2, 0).cpu().numpy()
        if not np.isfinite(raw_values).all() or not np.isfinite(raw_native).all():
            raise RuntimeError("ColorFM produced non-finite colors")
        values = np.clip(raw_values, 0, 1).astype(np.float32)
        native_output = np.clip(raw_native, 0, 1).astype(np.float32)

    if target.type == "cuda":
        torch.cuda.synchronize(target)
    end = time.perf_counter()
    metadata = {
        **_provenance(),
        "method": "ColorFM-L official checkpoint + frozen-kernel LUT bake",
        "torch_version": torch.__version__,
        "device": str(target),
        "gpu_name": torch.cuda.get_device_name(target) if target.type == "cuda" else None,
        "dtype": "float32",
        "input_space": "RGB [0,1] as official app (sRGB display images)",
        "lut_axis_order": "R,G,B,output_RGB",
        "lut_size": size,
        "content_shape": list(content_rgb.shape),
        "reference_shape": list(reference_rgb.shape),
        "native_raw_min": float(raw_native.min()),
        "native_raw_max": float(raw_native.max()),
        "native_out_of_range_channel_fraction": float(((raw_native < 0) | (raw_native > 1)).mean()),
        "lut_raw_min": float(raw_values.min()),
        "lut_raw_max": float(raw_values.max()),
        "lut_out_of_range_channel_fraction": float(((raw_values < 0) | (raw_values > 1)).mean()),
        "frozen_map_native_max_abs_error": reconstruction_max,
        "model_load_seconds": loaded - start,
        "inference_and_lut_seconds": end - loaded,
        "total_seconds": end - start,
        "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(target) if target.type == "cuda" else 0,
    }
    return {"values": values, "native_output": native_output, "metadata": metadata}
