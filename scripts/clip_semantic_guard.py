"""Add an explicit scene-content guard to already cached local CLIP pairs.

Only .npy embeddings, JSON metadata and the local official CLIP checkpoint are
read. This program never opens image files or calls the image encoder. Text
prompts name objects/scenes, not colors, grading, photographic style or mood.
Zero-shot categories are an automatic semantic check, not exact-object proof.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import shutil
import sys

import numpy as np
from scipy.optimize import linear_sum_assignment
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "CLIP"))

# These labels intentionally separate common landscape and transportation
# subjects that a generic 'outdoors' or 'travel' category would collapse.
SCENES = {
    "rocky_mountains": ("mountain ranges and rocky mountain peaks", "rugged mountains and mountain valleys", "a mountain landscape with exposed rock", "mountain summits above a valley"),
    "snowy_mountains": ("snow covered mountains and snowy peaks", "a snow capped mountain range", "an alpine mountain landscape covered in snow", "snowy mountain summits"),
    "desert_dunes": ("sand dunes in a desert", "a sandy desert landscape", "desert sand dunes and sand ridges", "a desert landscape with dunes"),
    "rock_canyon": ("a rocky canyon and cliffs", "a canyon with rock formations", "rock towers and a deep canyon", "a landscape of sandstone cliffs and canyons"),
    "forest_trees": ("a forest of trees", "woodland trees and forest vegetation", "a landscape dominated by a dense forest", "a forest landscape with tree trunks and foliage"),
    "grassland_fields": ("grassland and grassy hills", "a meadow and open fields", "a landscape of farm fields and grass", "rolling hills covered with grass"),
    "lake_pond": ("a lake surrounded by land", "a landscape with a lake and reflections", "the shore of a lake or pond", "a lake with distant hills"),
    "river_stream": ("a river winding through a landscape", "a flowing stream and river banks", "a river and its surrounding landscape", "a stream flowing between rocks"),
    "waterfall": ("a waterfall cascading over rocks", "a landscape with a waterfall", "falling water and a waterfall pool", "a waterfall in a forest or mountains"),
    "coast_ocean": ("an ocean coastline and beach", "a seascape with sea and coastal rocks", "ocean waves on the shore", "a coastal landscape beside the sea"),
    "glacier_ice": ("a glacier and glacial ice", "icebergs and a frozen landscape", "a landscape dominated by ice and glaciers", "ice formations on a frozen lake"),
    "urban_skyline": ("a city skyline with many tall buildings", "a cityscape of skyscrapers", "an aerial view of dense city buildings", "a panoramic city skyline"),
    "urban_street": ("a city street lined with buildings", "an urban street scene", "a street and sidewalks in a town", "an intersection between city buildings"),
    "building_architecture": ("the architecture of a building", "a building facade and architectural details", "a single building viewed from outside", "a church or historic building"),
    "rural_village": ("a rural village with houses", "a small village in the countryside", "traditional village houses in a landscape", "a rural settlement of homes"),
    "construction_industrial": ("a construction site with cranes", "industrial buildings and factory machinery", "a building under construction", "an industrial site with cranes and structures"),
    "bridge": ("a bridge crossing water", "the structure of a large bridge", "a bridge spanning a valley", "a bridge and its surroundings"),
    "railway_train": ("a train on railway tracks", "a railway station with trains", "railroad tracks through a landscape", "a locomotive and railway carriages"),
    "road_vehicles": ("a car or truck on a road", "motor vehicles on a roadway", "an automobile parked outside", "a road with cars and vehicles"),
    "road_landscape": ("a road winding through a landscape", "a rural road between hills", "a highway extending into the distance", "an empty road through the countryside"),
    "boat_harbor": ("boats on the water in a harbor", "a ship or sailboat on water", "a harbor with boats and docks", "a boat beside a waterfront"),
    "flowers_plants": ("flowers and plant leaves", "a close up of a flower", "a garden of flowers and plants", "plant blossoms and foliage"),
    "birds": ("a bird with wings and feathers", "birds perched on branches", "a bird standing or flying", "a wildlife photograph of birds"),
    "animals": ("an animal in its surroundings", "a wild animal or domestic animal", "a photograph of a mammal", "an animal standing in a landscape"),
    "portrait_single": ("a portrait of one person", "a close up of a person's face", "one person posing for a portrait", "a photograph focused on a human subject"),
    "people_group": ("a group of people together", "a crowd of people at an event", "several people walking or gathering", "people participating in an outdoor activity"),
    "food": ("food and a prepared meal", "a plate of food on a table", "food and drinks", "a close up photograph of a meal"),
    "interior": ("the inside of a room", "an indoor room with furniture", "the interior of a building", "an indoor architectural space"),
    "night_city": ("a city at night with illuminated buildings", "a night cityscape with buildings and streets", "a city street at night", "illuminated buildings in a nighttime city"),
    "sky_astronomy": ("stars and the Milky Way in the night sky", "a night sky with stars and a comet", "astronomical objects in the sky", "a starry sky above a landscape"),
    "clouds_sky": ("clouds filling the sky", "a cloudy sky above the horizon", "a photograph dominated by clouds", "cloud formations in the sky"),
}
LANDSCAPE_LABELS = {
    "rocky_mountains", "snowy_mountains", "desert_dunes", "rock_canyon",
    "forest_trees", "grassland_fields", "lake_pond", "river_stream",
    "waterfall", "coast_ocean", "glacier_ice", "rural_village", "road_landscape",
    "sky_astronomy", "clouds_sky",
}
STREET_EXTRA_SCENES = {
    "street_pedestrians": ("pedestrians walking on a city street", "people walking on urban sidewalks", "people crossing a street in a town", "everyday human activity on a street"),
    "street_market": ("people shopping at an outdoor street market", "vendors and shoppers at market stalls", "a crowded street market with people and stalls", "people buying and selling goods in a market street"),
    "bicycles_cyclists": ("people riding bicycles on a street", "a bicycle and a cyclist", "cyclists riding along a road", "bicycles parked or ridden in a city"),
    "street_public_transport": ("a tram or bus on a city street", "people beside a city bus or streetcar", "public transportation vehicles on a street", "a tram with passengers in a town"),
}
# Empty buildings, skyline, construction and generic portraits cannot by
# themselves pass the street-content guard. A street-specific label is needed.
STREET_LABELS = {
    "urban_street", "railway_train", "road_vehicles", "night_city",
    "street_pedestrians", "street_market", "bicycles_cyclists", "street_public_transport",
}


def write_all_labels(out, contents, references, cp, rp, names, prompts, scenes=None):
    scenes = SCENES if scenes is None else scenes
    taxonomy_hash = hashlib.sha256(json.dumps(scenes, sort_keys=True).encode()).hexdigest()
    for role, records, predictions in (("content", contents, cp), ("reference", references, rp)):
        labels = []
        for i, (record, prediction) in enumerate(zip(records, predictions)):
            domains = []
            if set(prediction["top2"]) & LANDSCAPE_LABELS:
                domains.append("landscape")
            if set(prediction["top2"]) & STREET_LABELS:
                domains.append("street")
            labels.append({"index": i, "sha256": record["sha256"], "path": record["path"],
                           "scene_domains_from_top2": domains, **prediction})
        (out / f"{role}_semantic_labels.json").write_text(json.dumps({
            "model": "ViT-B/32", "taxonomy_sha256": taxonomy_hash,
            "labels": names, "record_count": len(records), "predictions": labels,
            "matching_key": "sha256", "exact_object_guarantee": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return taxonomy_hash


def load_features(records, cache):
    vectors = []
    for record in records:
        path = cache / (record["sha256"] + ".npy")
        if not path.exists():
            raise FileNotFoundError(f"Missing image embedding; will not read image: {path}")
        vectors.append(np.load(path, allow_pickle=False))
    values = np.asarray(vectors, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != 512:
        raise ValueError(f"Unexpected ViT-B/32 feature shape: {values.shape}")
    if not np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-4):
        raise ValueError("Cached image features must already be L2 normalized")
    return values


def text_features(checkpoint, device, batch_size=32, scenes=None):
    import clip
    scenes = SCENES if scenes is None else scenes
    model, _ = clip.load(str(checkpoint), device=device, jit=False)
    model.eval()
    names = list(scenes)
    prompts = [f"A photo of {description}." for name in names for description in scenes[name]]
    batches = []
    with torch.inference_mode():
        for start in range(0, len(prompts), batch_size):
            tokens = clip.tokenize(prompts[start:start + batch_size]).to(device)
            features = model.encode_text(tokens).float()
            features /= features.norm(dim=1, keepdim=True)
            batches.append(features.cpu().numpy())
    values = np.concatenate(batches).reshape(len(names), 4, 512).mean(axis=1)
    values /= np.linalg.norm(values, axis=1, keepdims=True)
    gpu_peak = int(torch.cuda.max_memory_allocated()) if device == "cuda" else 0
    del model
    if device == "cuda":
        torch.cuda.empty_cache()
    return names, values.astype(np.float32), prompts, gpu_peak


def scene_predictions(features, names, text):
    scores = features @ text.T
    order = np.argsort(-scores, axis=1)
    predictions = []
    for score, idx in zip(scores, order):
        predictions.append({
            "top1": names[int(idx[0])], "top2": [names[int(i)] for i in idx[:2]],
            "top3": [names[int(i)] for i in idx[:3]],
            "top1_text_cosine": float(score[idx[0]]),
            "top2_text_cosines": [float(score[i]) for i in idx[:2]],
            "top1_top2_margin": float(score[idx[0]] - score[idx[1]]),
        })
    return scores, predictions


def match(scores, valid):
    # The cardinality reward exceeds the maximum possible total cosine change.
    penalty = float(min(scores.shape) + 1)
    row, col = linear_sum_assignment(np.where(valid, -penalty - scores, 0.0))
    result = [(float(scores[i, j]), int(i), int(j))
              for i, j in zip(row, col) if valid[i, j]]
    return sorted(result, reverse=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", "--output", dest="output", default="data/pairing")
    parser.add_argument("--pairs", type=int, default=50)
    parser.add_argument("--lower", type=float, default=.7)
    parser.add_argument("--upper", type=float, default=.9)
    parser.add_argument("--mode", choices=["auto", "same_top1", "top2_intersection"], default="auto")
    parser.add_argument("--domain", choices=["all", "landscape", "street"], default="all",
                        help="Optional shared-label domain filter; no quota or threshold relaxation")
    parser.add_argument("--pair-prefix", default="P")
    parser.add_argument("--exclude-content-pairs", type=Path,
                        help="JSON list of frozen pairs; their content SHA-256s cannot be reused")
    parser.add_argument("--embedding-cache", type=Path,
                        help="Shared existing local image-feature cache; never recompute image features")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()
    out = ROOT / args.output
    checkpoint = ROOT / "models/clip/ViT-B-32.pt"
    if not checkpoint.exists():
        raise FileNotFoundError("Local official CLIP checkpoint required; no download permitted by this script")
    contents = json.loads((out / "content_records.json").read_text(encoding="utf-8"))
    references = json.loads((out / "reference_records.json").read_text(encoding="utf-8"))
    cache = args.embedding_cache or (out / "ViT-B-32_embeddings")
    if not cache.is_absolute():
        cache = ROOT / cache
    c = load_features(contents, cache)
    r = load_features(references, cache)
    scores = np.load(out / "cosine_matrix.npy", allow_pickle=False)
    if scores.shape != (len(c), len(r)) or not np.allclose(c @ r.T, scores, atol=2e-5):
        raise ValueError("Cosine matrix does not correspond to the cached features/record order")
    scenes = {**SCENES, **STREET_EXTRA_SCENES} if args.domain == "street" else SCENES
    names, text, prompts, gpu_peak = text_features(checkpoint, args.device, scenes=scenes)
    cs, cp = scene_predictions(c, names, text)
    rs, rp = scene_predictions(r, names, text)
    raw_valid = (scores >= args.lower) & (scores <= args.upper)
    raw_edges_before_exclusions = int(raw_valid.sum())
    excluded_sha = set()
    if args.exclude_content_pairs:
        exclude_path = args.exclude_content_pairs
        if not exclude_path.is_absolute():
            exclude_path = ROOT / exclude_path
        frozen = json.loads(exclude_path.read_text(encoding="utf-8"))
        excluded_sha = {pair["content"]["sha256"] for pair in frozen}
        for i, record in enumerate(contents):
            if record["sha256"] in excluded_sha:
                raw_valid[i] = False
    top1 = np.asarray([[a["top1"] == b["top1"] for b in rp] for a in cp])
    top2 = np.asarray([[bool(set(a["top2"]) & set(b["top2"])) for b in rp] for a in cp])
    if args.domain != "all":
        domain_labels = LANDSCAPE_LABELS if args.domain == "landscape" else STREET_LABELS
        top1 &= np.asarray([[a["top1"] in domain_labels and b["top1"] in domain_labels
                             for b in rp] for a in cp])
        top2 &= np.asarray([[bool(set(a["top2"]) & set(b["top2"]) & domain_labels)
                             for b in rp] for a in cp])
    top1_edges = raw_valid & top1
    top2_edges = raw_valid & top2
    top1_matching = match(scores, top1_edges)
    top2_matching = match(scores, top2_edges)
    mode = args.mode
    if mode == "auto":
        mode = "same_top1" if len(top1_matching) >= args.pairs else "top2_intersection"
    valid = top1_edges if mode == "same_top1" else top2_edges
    matching = top1_matching if mode == "same_top1" else top2_matching
    chosen = matching[:args.pairs]
    pairs = []
    for k, (score, i, j) in enumerate(chosen):
        shared = sorted(set(cp[i]["top2"]) & set(rp[j]["top2"]))
        domain_shared = shared
        if args.domain != "all":
            allowed = LANDSCAPE_LABELS if args.domain == "landscape" else STREET_LABELS
            domain_shared = sorted(set(shared) & allowed)
        pairs.append({"pair_id": f"{args.pair_prefix}{k+1:03d}", "clip_cosine": score,
                      "content": contents[i], "reference": references[j],
                      "domain": args.domain,
                      "content_index": i, "reference_index": j,
                      "semantic_guard": {
                          "mode": mode, "passed": True, "shared_top2_labels": shared,
                          "matched_domain_labels": domain_shared,
                          "content": cp[i], "reference": rp[j],
                          "same_top1": bool(top1[i, j]),
                          "scope": "Scene/subject-content taxonomy; no style/color prompts"}})
    if not pairs:
        raise RuntimeError("No semantic pairs; retaining original pairs files")
    before = out / "pairs_before_semantic_guard.json"
    if not before.exists() and (out / "pairs.json").exists():
        shutil.copyfile(out / "pairs.json", before)
    (out / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "pairs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair_id", "clip_cosine", "content", "reference", "content_pixels",
                         "reference_pixels", "semantic_mode", "content_top1", "reference_top1", "shared_top2_labels"])
        for pair in pairs:
            a, b, guard = pair["content"], pair["reference"], pair["semantic_guard"]
            writer.writerow([pair["pair_id"], pair["clip_cosine"], a["path"], b["path"],
                             a["width"]*a["height"], b["width"]*b["height"], mode,
                             guard["content"]["top1"], guard["reference"]["top1"],
                             "|".join(guard["shared_top2_labels"])])
    selected_keys = {(p["content_index"], p["reference_index"]): p["pair_id"] for p in pairs}
    with (out / "semantic_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["content_index", "reference_index", "raw_image_cosine", "content_top1",
                         "content_top2", "reference_top1", "reference_top2", "same_top1",
                         "top2_intersects", "shared_top2", "selected_guard_pass", "selected_pair_id"])
        for i, j in np.argwhere(raw_valid):
            i, j = int(i), int(j)
            shared = sorted(set(cp[i]["top2"]) & set(rp[j]["top2"]))
            writer.writerow([i, j, float(scores[i, j]), cp[i]["top1"], "|".join(cp[i]["top2"]),
                             rp[j]["top1"], "|".join(rp[j]["top2"]), bool(top1[i, j]),
                             bool(top2[i, j]), "|".join(shared), bool(valid[i, j]), selected_keys.get((i, j), "")])
    np.save(out / "semantic_text_embeddings.npy", text)
    np.save(out / "content_scene_text_cosines.npy", cs)
    np.save(out / "reference_scene_text_cosines.npy", rs)
    (out / "semantic_taxonomy.json").write_text(json.dumps({"labels": names, "prompts": prompts,
        "content_predictions": cp, "reference_predictions": rp}, ensure_ascii=False, indent=2), encoding="utf-8")
    taxonomy_hash = write_all_labels(out, contents, references, cp, rp, names, prompts, scenes)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    summary["before_semantic_guard"] = {k: summary.get(k) for k in (
        "eligible_edges", "maximum_unique_matching_count", "selected_pairs", "selection")}
    summary.update({"eligible_edges": int(valid.sum()), "maximum_unique_matching_count": len(matching),
                    "selected_pairs": len(pairs), "requested_pairs": args.pairs,
                    "selection": "Semantic content guard, then maximum cardinality and total raw image cosine; top requested unique pairs",
                    "semantic_guard": {
                        "mode": mode, "taxonomy_categories": len(names), "prompts_per_category": 4,
                        "domain": args.domain, "taxonomy_sha256": taxonomy_hash,
                        "raw_threshold_edges": int(raw_valid.sum()),
                        "raw_threshold_edges_before_frozen_exclusion": raw_edges_before_exclusions,
                        "excluded_frozen_content_sha256_count": len(excluded_sha),
                        "excluded_frozen_content_present_in_input_count": sum(r["sha256"] in excluded_sha for r in contents),
                        "same_top1_edges": int(top1_edges.sum()), "same_top1_maximum_matching": len(top1_matching),
                        "top2_intersection_edges": int(top2_edges.sum()), "top2_intersection_maximum_matching": len(top2_matching),
                        "selected_same_top1": sum(p["semantic_guard"]["same_top1"] for p in pairs),
                        "selected_label_counts": dict(Counter(p["semantic_guard"]["content"]["top1"] for p in pairs)),
                        "text_prompts_use_style_or_color": False,
                        "image_files_read_this_run": 0, "image_encoder_calls_this_run": 0,
                        "local_text_inference_only": True, "device": args.device,
                        "torch_peak_gpu_allocated_bytes": gpu_peak,
                        "exact_object_identity_guaranteed": False,
                        "caveat": "Automatic scene labels may err; agreement is semantic evidence, not exact-object or human-verified equivalence",
                        "completed_at": datetime.now(timezone.utc).isoformat()},
                    "threshold_relaxed": False})
    if args.domain == "street":
        summary["reference_resolution_exception"] = (
            "Public Magnum/Leica street references retained at actual published resolution; never upscaled")
        summary["semantic_guard"]["selected_reference_publishers"] = dict(Counter(
            p["reference"].get("publisher", "unknown") for p in pairs))
        summary["semantic_guard"]["selected_reference_photographers"] = dict(Counter(
            p["reference"].get("photographer", "unknown") for p in pairs))
    assert len({p["content"]["sha256"] for p in pairs}) == len(pairs)
    assert len({p["reference"]["sha256"] for p in pairs}) == len(pairs)
    assert all(args.lower <= p["clip_cosine"] <= args.upper for p in pairs)
    assert all(p["semantic_guard"]["shared_top2_labels"] for p in pairs)
    assert all(p["content"]["sha256"] not in excluded_sha for p in pairs)
    assert all(p["semantic_guard"]["matched_domain_labels"] for p in pairs)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"selected_pairs": len(pairs), "maximum_matching": len(matching),
                      "mode": mode, "same_top1_edges": int(top1_edges.sum()),
                      "same_top1_maximum_matching": len(top1_matching),
                      "top2_edges": int(top2_edges.sum()), "top2_maximum_matching": len(top2_matching),
                      "raw_threshold_edges": int(raw_valid.sum()),
                      "selected_min_cosine": min(p["clip_cosine"] for p in pairs),
                      "selected_max_cosine": max(p["clip_cosine"] for p in pairs),
                      "gpu_peak_bytes": gpu_peak}), flush=True)


if __name__ == "__main__":
    main()
