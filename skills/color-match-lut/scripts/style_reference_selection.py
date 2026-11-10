"""Rank researched colour references by recurring look, then light and subject."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from urllib.parse import urlsplit


FORMAT = "color-style-reference-selection-v1"


def select_references(research):
    """Select from distinct, source-checked stills described by the host agent."""
    style_text = research["style_text"].strip()
    if not style_text:
        raise ValueError("style_text must be the user's nonempty request")

    content = research["content"]
    candidates = research["candidates"]
    eligible = []
    seen = set()
    scenes_by_look = {}
    for shot in candidates:
        if shot.get("exclude_reason"):
            continue
        shot_id, work, scene, source = (shot[key].strip() for key in ("id", "work", "scene", "source_url"))
        url = urlsplit(source)
        if not all((shot_id, work, scene)) or url.scheme not in ("https", "http") or not url.hostname:
            raise ValueError("each eligible shot needs an ID, work, scene and verifiable source URL")
        if shot_id in seen:
            raise ValueError(f"duplicate eligible shot ID: {shot_id}")
        seen.add(shot_id)
        eligible.append(shot)
        if look := shot.get("look"):
            scenes_by_look.setdefault(look, set()).add((work, scene))

    look_counts = Counter(shot["look"] for shot in eligible if shot.get("look"))
    total = len(eligible)
    decision = {
        "format": FORMAT,
        "style_text": style_text,
        "content": content,
        "source_scope": research.get("source_scope"),
        "queries": research.get("queries", []),
        "candidate_count": len(candidates),
        "eligible_count": total,
        "looks": [
            {"look": look, "support": count, "independent_scenes": len(scenes_by_look[look]),
             "sample_ids": [shot["id"] for shot in eligible if shot.get("look") == look]}
            for look, count in look_counts.most_common()
        ],
        "status": "insufficient",
        "selected": None,
        "candidates": candidates,
    }
    if not look_counts:
        return decision
    (dominant, count), *others = look_counts.most_common()
    if others and count == others[0][1]:
        decision["status"] = "tied"
        return decision
    if len(scenes_by_look[dominant]) < 2:
        return decision
    pool = [shot for shot in eligible if shot.get("look") == dominant]
    subjects = set(content.get("subjects", []))

    def match(shot):
        same_light = bool(content.get("light")) and shot.get("light") == content["light"]
        shared_subjects = sorted(subjects.intersection(shot.get("subjects", [])))
        return same_light, shared_subjects

    def rank(shot):
        same_light, shared_subjects = match(shot)
        return int(same_light) + int(bool(shared_subjects)), same_light, len(shared_subjects)

    selected = max(pool, key=rank)
    same_light, shared_subjects = match(selected)
    decision.update(
        status="majority" if 2 * count > total else "recurring_plurality",
        selected={**selected, "same_light": same_light, "shared_subjects": shared_subjects},
    )
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    research = json.loads(args.candidates.read_text(encoding="utf-8-sig"))
    result = select_references(research)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
