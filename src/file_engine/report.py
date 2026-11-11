"""Write what happened (or would happen) as JSON, Markdown and CSV.

File names are attacker-controlled text: every one goes through safety.* first.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from . import safety
from .executor import ApplyResult
from .planner import Plan


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"  # pragma: no cover


def _rel(path: Path, plan: Plan) -> str:
    for base in (plan.root, plan.dest):
        if path.is_relative_to(base):
            return str(path.relative_to(base))
    return str(path)


def summarise(plan: Plan, applied: ApplyResult | None = None) -> dict:
    naive = plan.scan.total_bytes
    dup = plan.duplicates
    summary = {
        "mode": "applied" if applied else "dry-run",
        "root": str(plan.root),
        "dest": str(plan.dest),
        "scanned": {
            "files": len(plan.scan.files),
            "bytes": naive,
            "folders": plan.scan.dirs_seen,
            "skipped": dict(Counter(s.reason.split(" ")[0] for s in plan.scan.skipped).most_common()),
        },
        "categories": {
            c: {"files": plan.category_files[c], "bytes": plan.category_bytes[c]}
            for c, _ in plan.category_files.most_common()
        },
        "duplicates": {
            "groups": len(dup.groups),
            "copies": sum(len(g.copies) for g in dup.groups),
            "wasted_bytes": sum(g.wasted_bytes for g in dup.groups),
            "bytes_read_to_find_them": dup.bytes_read,
            "bytes_a_naive_full_hash_reads": naive,
            "files_edge_hashed": dup.files_edge_hashed,
            "files_full_hashed": dup.files_full_hashed,
            "hash_errors": len(dup.errors),
            "top": [
                {
                    "keep": _rel(g.keeper.path, plan),
                    "copies": [_rel(c.path, plan) for c in g.copies],
                    "size": g.keeper.size,
                    "sha256": g.digest,
                }
                for g in dup.groups[:20]
            ],
        },
        "plan": {
            "moves": len(plan.moves),
            "renames": plan.renames,
            "quarantined": len(plan.quarantines),
            "already_in_place": plan.unchanged,
            "refused": [{"path": _rel(p, plan), "why": why} for p, why in plan.refused],
        },
    }
    if applied:
        summary["applied"] = {
            "journal": str(applied.journal),
            "counts": dict(applied.counts),
            "problems": [
                {"src": _rel(o.action.src, plan), "status": o.status, "why": o.detail}
                for o in applied.outcomes
                if o.status != "moved"
            ],
        }
    return summary


def to_markdown(plan: Plan, summary: dict, *, max_rows: int = 50) -> str:
    md, s, d = safety.md_cell, summary["scanned"], summary["duplicates"]
    title = "Applied" if summary["mode"] == "applied" else "Dry run (nothing has been changed)"
    lines = [
        f"# File automation report: {title}",
        "",
        f"**Folder:** {md(summary['root'])}  ",
        f"**Organised into:** {md(summary['dest'])}",
        "",
        "## At a glance",
        "",
        "| | |",
        "|---|---|",
        f"| Files scanned | {s['files']:,} ({human_bytes(s['bytes'])}) in {s['folders']:,} folders |",
        f"| Skipped (not touched) | {sum(s['skipped'].values()):,} "
        + (f"({', '.join(f'{k}: {v}' for k, v in s['skipped'].items())})" if s["skipped"] else "")
        + " |",
        f"| Duplicate copies | {d['copies']:,} in {d['groups']:,} groups, "
        f"**{human_bytes(d['wasted_bytes'])} reclaimable** |",
        f"| Bytes read to find them | {human_bytes(d['bytes_read_to_find_them'])} "
        f"(hashing everything would read {human_bytes(d['bytes_a_naive_full_hash_reads'])}) |",
        f"| Planned moves | {summary['plan']['moves']:,} ({summary['plan']['renames']:,} renamed) |",
        f"| Copies to quarantine | {summary['plan']['quarantined']:,} |",
        f"| Already in place | {summary['plan']['already_in_place']:,} |",
    ]
    if "applied" in summary:
        a = summary["applied"]
        lines.append(f"| Result | {', '.join(f'{k}: {v}' for k, v in a['counts'].items()) or 'nothing to do'} |")
        lines.append(f"| Undo with | file-engine undo {md(a['journal'])} --apply |")

    lines += ["", "## By category", "", "| Category | Files | Size |", "|---|---:|---:|"]
    for cat, v in summary["categories"].items():
        lines.append(f"| {md(cat)} | {v['files']:,} | {human_bytes(v['bytes'])} |")

    if d["top"]:
        lines += ["", "## Biggest duplicate groups", "", "| Keep | Copies | Size each |", "|---|---|---:|"]
        for g in d["top"][:10]:
            copies = "<br>".join(md(c) for c in g["copies"])
            lines.append(f"| {md(g['keep'])} | {copies} | {human_bytes(g['size'])} |")

    lines += ["", f"## Planned actions (first {max_rows})", "", "| Action | From | To |", "|---|---|---|"]
    for a in plan.actions[:max_rows]:
        lines.append(f"| {a.kind.value} | {md(_rel(a.src, plan))} | {md(_rel(a.dst, plan))} |")
    if len(plan.actions) > max_rows:
        lines.append(f"| … | {len(plan.actions) - max_rows:,} more in actions.csv | |")

    problems = summary["plan"]["refused"] + summary.get("applied", {}).get("problems", [])
    if problems:
        lines += ["", "## Needs a human", "", "| File | What happened |", "|---|---|"]
        for p in problems:
            lines.append(f"| {md(p.get('path') or p.get('src'))} | {md(p['why'])} |")
    return "\n".join(lines) + "\n"


def write_reports(plan: Plan, out_dir: Path, applied: ApplyResult | None = None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarise(plan, applied)
    paths = {
        "json": out_dir / "report.json",
        "markdown": out_dir / "report.md",
        "csv": out_dir / "actions.csv",
    }
    paths["json"].write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["markdown"].write_text(to_markdown(plan, summary), encoding="utf-8")
    status = {id(o.action): o for o in applied.outcomes} if applied else {}
    with paths["csv"].open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["action", "from", "to", "category", "bytes", "status", "detail"])
        for a in plan.actions:
            o = status.get(id(a))
            w.writerow(
                [
                    a.kind.value,
                    safety.csv_cell(_rel(a.src, plan)),
                    safety.csv_cell(_rel(a.dst, plan)),
                    a.category,
                    a.size,
                    o.status if o else "planned",
                    safety.csv_cell(o.detail) if o else "",
                ]
            )
    return paths
