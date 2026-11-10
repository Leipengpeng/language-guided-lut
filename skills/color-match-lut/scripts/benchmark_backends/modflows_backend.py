"""Official ModFlows B6 encoder and frozen pointwise flow-to-LUT adapter.

Source/reference encodings are computed only from their actual images.  The
resulting flow parameters are frozen before evaluating source pixels or a LUT.
No visual API or remote inference is involved.
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from . import backend_root

ROOT = backend_root()
SOURCE = ROOT / "vendor" / "modflows"
CHECKPOINT = ROOT / "models" / "ModFlows_B6_751001.pt"
CHECKPOINT_URL = "https://huggingface.co/MariaLarchenko/modflows_color_encoder/resolve/main/modflows_color_encoder_B6_dim_8195_iter_751001.pt"


def _load_source(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _source_modules():
    # Direct file imports avoid the generic upstream `src` namespace and its
    # inference module's matplotlib dependency. Source files remain unchanged.
    return (
        _load_source("_benchmark_modflows_encoder", SOURCE / "src" / "encoder.py"),
        _load_source("_benchmark_modflows_ode", SOURCE / "src" / "neural_ode.py"),
    )


@lru_cache(maxsize=1)
def _provenance():
    with CHECKPOINT.open("rb") as checkpoint_file:
        checkpoint_sha = hashlib.file_digest(checkpoint_file, "sha256").hexdigest()
    return {
        "repository": "https://github.com/maria-larchenko/modflows",
        "source_commit": "ca5da1a2742fb6a63e03b7a1245662f47d278b24",
        "checkpoint_url": CHECKPOINT_URL,
        "checkpoint": str(CHECKPOINT),
        "checkpoint_bytes": CHECKPOINT.stat().st_size,
        "checkpoint_sha256": checkpoint_sha,
        "checkpoint_license": "MIT (official Hugging Face model card)",
        "code_license": "MIT",
        "source_patches": [],
        "source_files_sha256": {
            str(path.relative_to(SOURCE)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (SOURCE / "src" / "encoder.py", SOURCE / "src" / "neural_ode.py")
        },
    }


@lru_cache(maxsize=1)
def _load_model(device_name: str):
    encoder_source, _ = _source_modules()
    device = torch.device(device_name)
    model = encoder_source.Encoder(
        k_dim=8195, input_dim=4, hidden=1024, output_dim=3,
        device=device, encoder_type="B6",
    )
    model.load_state_dict(torch.load(CHECKPOINT, map_location="cpu", weights_only=True), strict=True)
    return model.to(device).eval(), device


def _validate(image, name):
    image = np.asarray(image, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3 or min(image.shape[:2]) < 1:
        raise ValueError(f"{name} must be nonempty HWC RGB")
    if not np.isfinite(image).all() or image.min() < 0 or image.max() > 1:
        raise ValueError(f"{name} must be finite sRGB in [0,1]")
    return np.ascontiguousarray(image)


def _map(points, base_flow, target_flow, device, steps):
    output = np.empty_like(points, dtype=np.float32)
    for offset in range(0, len(points), 16384):
        chunk = torch.from_numpy(np.ascontiguousarray(points[offset:offset + 16384])).to(device)
        latent = base_flow.sample(chunk, N=steps, strength=1.0)
        styled = target_flow.inv_sample(latent, N=steps, strength=1.0)
        output[offset:offset + len(chunk)] = styled.cpu().numpy()
    return output


def fit_lut(content_rgb, reference_rgb, size=33, *, device=None, steps=8):
    """Return [R,G,B,RGB] sRGB LUT, native output, and exact source provenance.

    Run on analysis images <=1024 px for the benchmark, then apply the exported
    LUT to the full-resolution image. Official B6 preprocessing is retained,
    including the released HWC-to-CHW reshape (rather than a transpose).
    """
    content = _validate(content_rgb, "content_rgb")
    reference = _validate(reference_rgb, "reference_rgb")
    if not isinstance(size, int) or not 2 <= size <= 129:
        raise ValueError("size must be an integer in [2,129]")
    if not isinstance(steps, int) or not 2 <= steps <= 100:
        raise ValueError("steps must be an integer in [2,100]")
    start = time.perf_counter()
    model, target = _load_model(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    encoder_source, ode_source = _source_modules()
    loaded = time.perf_counter()

    def encode(rgb):
        pil = Image.fromarray(np.rint(rgb * 255).astype(np.uint8))
        tensor = encoder_source.enc_preprocess(pil).unsqueeze(0).to(target)
        return model(tensor).flatten()

    with torch.inference_mode():
        base_flow = ode_source.NeuralODE(input_dim=3, hidden=model.hidden, device=target)
        target_flow = ode_source.NeuralODE(input_dim=3, hidden=model.hidden, device=target)
        base_flow.set_weights(encode(content))
        target_flow.set_weights(encode(reference))
        # Source/ref embeddings are now frozen. Never encode identity LUTs.
        native_raw = _map(content.reshape(-1, 3), base_flow, target_flow, target, steps).reshape(content.shape)
        axis = np.linspace(0, 1, size, dtype=np.float32)
        grid = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
        values_raw = _map(grid.reshape(-1, 3), base_flow, target_flow, target, steps).reshape(grid.shape)
    if not np.isfinite(native_raw).all() or not np.isfinite(values_raw).all():
        raise RuntimeError("ModFlows returned nonfinite colors")
    if target.type == "cuda":
        torch.cuda.synchronize(target)
    return {
        "values": np.clip(values_raw, 0, 1).astype(np.float32),
        "native_output": np.clip(native_raw, 0, 1).astype(np.float32),
        "metadata": {
            **_provenance(), "method": "ModFlows official B6 iter751001, frozen bidirectional flow bake",
            "device": str(target), "dtype": "float32", "torch_version": torch.__version__,
            "gpu_name": torch.cuda.get_device_name(target) if target.type == "cuda" else None,
            "content_shape": list(content.shape), "reference_shape": list(reference.shape),
            "input_space": "sRGB display RGB [0,1] as official source",
            "output_space": "sRGB display RGB [0,1]", "lut_axis_order": "R,G,B,output_RGB",
            "lut_size": size, "euler_steps_each_direction": steps, "strength": 1.0,
            "encoder_input": "official528px resize, HWC.reshape(CHW), then EfficientNet_B6 transforms",
            "pretrained_backbone_download": False,
            "native_raw_min": float(native_raw.min()), "native_raw_max": float(native_raw.max()),
            "lut_raw_min": float(values_raw.min()), "lut_raw_max": float(values_raw.max()),
            "native_out_of_range_channel_fraction": float(((native_raw < 0) | (native_raw > 1)).mean()),
            "lut_out_of_range_channel_fraction": float(((values_raw < 0) | (values_raw > 1)).mean()),
            "model_load_seconds": loaded - start, "total_seconds": time.perf_counter() - start,
        },
    }
