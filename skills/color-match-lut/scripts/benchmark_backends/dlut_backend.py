"""D-LUT score matching and Langevin LUT sampling from the official release.

This is per-reference neural optimization, not a pretrained model. The official
EnergyScore, ToyMLP, training loop and Langevin routine are imported unchanged.
Plotting, TensorBoard and image-file bookkeeping are omitted by this adapter.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from PIL import Image
import torch
from torch.nn import functional as F
from torch.utils.data import DataLoader, TensorDataset
from . import backend_root

ROOT = backend_root()
SOURCE = ROOT / "vendor" / "D-LUT"
CACHE = ROOT / "models" / "D-LUT-reference-cache"


def _load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _sources():
    return (
        _load_file("_benchmark_dlut_models", SOURCE / "codebase" / "models" / "toy_models.py"),
        _load_file("_benchmark_dlut_runner", SOURCE / "codebase" / "runner.py"),
    )


def _validate(image, name):
    image = np.asarray(image, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3 or min(image.shape[:2]) < 1:
        raise ValueError(f"{name} must be nonempty HWC RGB")
    if not np.isfinite(image).all() or image.min() < 0 or image.max() > 1:
        raise ValueError(f"{name} must be finite sRGB in [0,1]")
    return np.ascontiguousarray(image)


def _save_checkpoint(model, checkpoint):
    state = {key: value.detach().cpu() for key, value in model.state_dict().items()}
    temporary = None
    try:
        with NamedTemporaryFile(dir=checkpoint.parent, suffix=".pth.tmp", delete=False) as stream:
            temporary = Path(stream.name)
            torch.save(state, stream)
        os.replace(temporary, checkpoint)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _apply_native(rgb, lut_bgr):
    """Preserve the release's grid_sample align_corners=False convention."""
    output = np.empty_like(rgb)
    for row in range(0, rgb.shape[0], 256):
        chunk = torch.from_numpy(np.ascontiguousarray(rgb[row:row + 256])).to(lut_bgr.device)
        grid = (chunk * 2 - 1)[None, None]
        result = F.grid_sample(
            lut_bgr, grid, mode="bilinear", padding_mode="border", align_corners=False,
        )[0, :, 0].permute(1, 2, 0)
        output[row:row + len(chunk)] = result.cpu().numpy()
    return output


def fit_lut(content_rgb, reference_rgb, size=33, *, device=None, epochs=15,
            seed=12213, native_size=15, langevin_steps=40, samples=50):
    """Fit the reference score and return a portable [R,G,B,RGB] LUT.

    Defaults reproduce the released demo's algorithm settings: 400x300 style
    image, 15 epochs, batch128, Adam1e-3, sigma0.005, 40 Langevin steps with
    eps0.0002, 50 trajectories on a 15^3 lattice. The mean native LUT is frozen
    and rebaked to the requested grid while preserving native interpolation.
    """
    content = _validate(content_rgb, "content_rgb")
    reference = _validate(reference_rgb, "reference_rgb")
    if not isinstance(size, int) or not 2 <= size <= 129:
        raise ValueError("size must be an integer in [2,129]")
    if epochs < 1 or samples < 1 or langevin_steps < 1 or native_size < 2:
        raise ValueError("epochs, samples, Langevin steps and native size must be positive")
    start = time.perf_counter()
    target = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    models, runners = _sources()
    ref_pil = Image.fromarray(np.rint(reference * 255).astype(np.uint8))
    ref_pixels = np.asarray(ref_pil.resize((400, 300), Image.Resampling.BICUBIC), dtype=np.float32) / 255
    settings = {
        "epochs": epochs, "style_size": [400, 300], "batch_size": 128,
        "learning_rate": 0.001, "sigma": 0.005, "seed": seed,
    }
    cache_key = hashlib.sha256(ref_pixels.tobytes() + json.dumps(settings, sort_keys=True).encode()).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    checkpoint = CACHE / f"{cache_key}.pth"
    cuda_devices = [target.index or 0] if target.type == "cuda" else []
    with torch.random.fork_rng(devices=cuda_devices):
        torch.manual_seed(seed)
        model = models.EnergyScore(models.ToyMLP(3, 3, swish=True)).to(target)
        cache_hit = checkpoint.is_file()
        train_started = time.perf_counter()
        if cache_hit:
            model.load_state_dict(torch.load(checkpoint, map_location=target, weights_only=True), strict=True)
        else:
            pixels = torch.from_numpy(ref_pixels.reshape(-1, 3).copy()).to(target)
            train_loader = DataLoader(TensorDataset(pixels), batch_size=128, shuffle=True)
            args = argparse.Namespace(
                train_epochs=epochs, learning_rate=0.001,
                save_epoch=epochs + 1, device=target,
            )
            runners.BasicRunner.train(args, model, train_loader, sigma=0.005)
            _save_checkpoint(model, checkpoint)
            del pixels, train_loader
        trained = time.perf_counter()
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        # Separate stochastic sampler seed makes new/cache-hit runs identical.
        torch.manual_seed(seed + 1)
        axis = torch.linspace(0, 1, native_size, device=target)
        blue, green, red = torch.meshgrid(axis, axis, axis, indexing="ij")
        identity_bgr = torch.stack((red, green, blue), dim=-1)
        trajectories = identity_bgr.unsqueeze(0).repeat(samples, 1, 1, 1, 1)
        sampled = runners.BasicRunner.langevin_dynamics(
            model, trajectories, L_steps=langevin_steps, eps=0.0002, keep_path=False,
        )
        mean_lut = sampled.detach().mean(dim=0).permute(3, 0, 1, 2).unsqueeze(0).contiguous()
        with torch.no_grad():
            native_raw = _apply_native(content, mean_lut)
            axis_np = np.linspace(0, 1, size, dtype=np.float32)
            grid = np.stack(np.meshgrid(axis_np, axis_np, axis_np, indexing="ij"), axis=-1)
            values_raw = _apply_native(grid.reshape(size * size, size, 3), mean_lut).reshape(size, size, size, 3)
        del sampled, trajectories, mean_lut, model
    if not np.isfinite(values_raw).all() or not np.isfinite(native_raw).all():
        raise RuntimeError("D-LUT returned nonfinite colors")
    if target.type == "cuda":
        torch.cuda.synchronize(target)
    return {
        "values": np.clip(values_raw, 0, 1).astype(np.float32),
        "native_output": np.clip(native_raw, 0, 1).astype(np.float32),
        "metadata": {
            "repository": "https://github.com/leemojiang/D-LUT",
            "source_commit": "01927996eb03ad21be7f9cdbeb7a4c6604e7431c",
            "code_license": "Apache-2.0", "method": "D-LUT official EnergyScore, per-reference training and mean Langevin LUT",
            "pretrained_checkpoint": None, "training_settings": settings,
            "content_conditioning": "Reference-only score projection as released; source colors query the learned map",
            "checkpoint": str(checkpoint), "checkpoint_cache_hit": cache_hit,
            "checkpoint_origin": "Locally trained on this reference; no general pretrained weights",
            "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            "native_lut_size": native_size, "lut_size": size,
            "langevin_steps": langevin_steps, "langevin_epsilon": 0.0002,
            "langevin_trajectories": samples, "langevin_seed": seed + 1,
            "input_space": "sRGB display RGB [0,1]", "output_space": "sRGB display RGB [0,1]",
            "native_interpolation": "PyTorch grid_sample bilinear, border, align_corners=False (official convention)",
            "lut_axis_order": "R,G,B,output_RGB", "device": str(target),
            "gpu_name": torch.cuda.get_device_name(target) if target.type == "cuda" else None,
            "content_shape": list(content.shape), "reference_shape": list(reference.shape),
            "training_seconds": trained - train_started,
            "total_seconds": time.perf_counter() - start,
            "source_patches": [],
            "adapter_changes": ["Omit plotting/logging; use independent fixed sampler seed for cache reproducibility; bake official interpolation convention into portable cube"],
            "native_raw_min": float(native_raw.min()), "native_raw_max": float(native_raw.max()),
            "lut_raw_min": float(values_raw.min()), "lut_raw_max": float(values_raw.max()),
            "native_out_of_range_channel_fraction": float(((native_raw < 0) | (native_raw > 1)).mean()),
        },
    }
