"""Local-only CLIP image embeddings and unique cross-source cosine pairing.

No image data, embeddings, or API calls leave this process. Downloads are the
official model checkpoint only. Cosine is unscaled, not a softmax probability.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps, ImageCms
from scipy.optimize import linear_sum_assignment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor/CLIP"))


def load_manifest(path, min_pixels):
    records, hashes = [], set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get('status') != 'ok':
            continue
        p = Path(record.get("path", record.get("local_path", "")))
        if not p.is_absolute():
            p = ROOT / p
        if not p.is_file() or p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
            continue
        with Image.open(p) as im:
            w, h = im.size
        if w*h < min_pixels:
            continue
        digest = record.get("sha256") or hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in hashes:
            continue
        hashes.add(digest)
        records.append({**record, "path": str(p.resolve()), "width": w, "height": h, "sha256": digest})
    return records


def srgb_image(path):
    import io
    with Image.open(path) as original:
        im = ImageOps.exif_transpose(original).convert("RGB")
        profile = original.info.get("icc_profile")
        if profile:
            im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                           ImageCms.createProfile("sRGB"), outputMode="RGB")
        return im


def embed(records, model, preprocess, cache_dir, device, batch_size=16):
    result = [None] * len(records)
    missing = []
    for i, item in enumerate(records):
        p = cache_dir / (item["sha256"] + ".npy")
        if p.exists():
            result[i] = np.load(p)
        else:
            missing.append(i)
    for start in range(0, len(missing), batch_size):
        indices = missing[start:start + batch_size]
        tensors = [preprocess(srgb_image(records[i]["path"])) for i in indices]
        with torch.inference_mode():
            features = model.encode_image(torch.stack(tensors).to(device)).float()
            features /= features.norm(dim=-1, keepdim=True)
        for i, vector in zip(indices, features.cpu().numpy()):
            result[i] = vector
            np.save(cache_dir / (records[i]["sha256"] + ".npy"), vector)
        print(json.dumps({"event":"embedded", "done":min(start+batch_size,len(missing)), "missing_total":len(missing)}), flush=True)
    return np.asarray(result, dtype=np.float32)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-manifest", default="data/dpreview_manifest.jsonl")
    parser.add_argument("--reference-manifest", default="data/thomas_manifest.jsonl")
    parser.add_argument("--output", default="data/pairing")
    parser.add_argument("--min-pixels", type=int, default=8294400)
    parser.add_argument("--reference-min-pixels", type=int, default=0,
                        help="Thomas public images may be below 4K by explicit user preference")
    parser.add_argument("--lower", type=float, default=.7)
    parser.add_argument("--upper", type=float, default=.9)
    parser.add_argument("--pairs", type=int, default=50)
    parser.add_argument("--model", default="ViT-B/32")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--embedding-cache",default=None)
    args=parser.parse_args()
    import clip
    device="cuda" if torch.cuda.is_available() else "cpu"
    model,preprocess=clip.load(args.model, device=device, jit=False, download_root=str(ROOT/"models/clip"))
    print(json.dumps({"event":"local_model_loaded", "model":args.model,"device":device}), flush=True)
    if args.download_only:
        return
    out=ROOT/args.output;out.mkdir(parents=True,exist_ok=True)
    cache=(ROOT/args.embedding_cache) if args.embedding_cache else out/(args.model.replace("/","-")+"_embeddings")
    cache.mkdir(parents=True,exist_ok=True)
    contents=load_manifest(ROOT/args.content_manifest,args.min_pixels)
    raw_content_count=len(contents)
    contents=[x for x in contents if x.get('processing_class')!='raw_conversion_or_editor']
    references=load_manifest(ROOT/args.reference_manifest,args.reference_min_pixels)
    if not contents or not references:
        raise RuntimeError(f"No eligible cross-source pool: content={len(contents)}, reference={len(references)}")
    c=embed(contents,model,preprocess,cache,device)
    r=embed(references,model,preprocess,cache,device)
    (out/'content_records.json').write_text(json.dumps(contents,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'reference_records.json').write_text(json.dumps(references,ensure_ascii=False,indent=2),encoding='utf-8')
    scores=np.clip(c@r.T,-1,1)
    np.save(out/"cosine_matrix.npy",scores)
    valid=(scores>=args.lower)&(scores<=args.upper)
    cost=np.where(valid,-1000.0-scores,0.0)
    row,col=linear_sum_assignment(cost)
    edges=[(float(scores[i,j]),int(i),int(j)) for i,j in zip(row,col) if valid[i,j]]
    edges.sort(reverse=True)
    chosen=edges[:args.pairs]
    pairs=[{"pair_id":f"P{k+1:03d}", "clip_cosine":score,
            "content":contents[i], "reference":references[j]} for k,(score,i,j) in enumerate(chosen)]
    (out/"pairs.json").write_text(json.dumps(pairs,ensure_ascii=False,indent=2),encoding="utf-8")
    with (out/"pairs.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f);w.writerow(["pair_id","clip_cosine","content","reference","content_pixels","reference_pixels"])
        for p in pairs:w.writerow([p["pair_id"],p["clip_cosine"],p["content"]["path"],p["reference"]["path"],p["content"]["width"]*p["content"]["height"],p["reference"]["width"]*p["reference"]["height"]])
    with (out/"eligible_candidates.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f);w.writerow(["content_index","reference_index","raw_cosine"])
        for i,j in np.argwhere(valid):w.writerow([int(i),int(j),float(scores[i,j])])
    source_provenance=json.loads((ROOT/'vendor/CLIP/source_provenance.json').read_text()) if (ROOT/'vendor/CLIP/source_provenance.json').exists() else {}
    summary={"local_only":True,"model":args.model,"repository":"https://github.com/openai/CLIP",
             "torch":torch.__version__,"source_provenance":source_provenance,
             "content_minimum_pixels":args.min_pixels,"reference_minimum_pixels":args.reference_min_pixels,
             "reference_resolution_exception":"User prioritizes Thomas authorship; public images below 4K, including ~1080, are permitted",
             "cosine_range":[args.lower,args.upper],
             "score_semantics":"dot product of L2-normalized image embeddings; no scaling or softmax",
             "content_count":len(contents),"reference_count":len(references),
             "excluded_content_editor_exif_count":raw_content_count-len(contents),
             "eligible_edges":int(valid.sum()),"maximum_unique_matching_count":len(edges),
             "requested_pairs":args.pairs,"selected_pairs":len(pairs),
             "score_quantiles":{str(q):float(np.quantile(scores,q)) for q in [0,.25,.5,.75,.9,.95,1]},
             "selection":"maximum cardinality then total cosine bipartite matching; top requested unique pairs",
             "threshold_relaxed":False}
    (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=="__main__":main()
