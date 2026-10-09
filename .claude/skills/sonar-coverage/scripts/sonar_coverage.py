#!/usr/bin/env python3
"""Calcola la coverage secondo la formula SonarQube a partire da report JaCoCo XML.

    Coverage = (CT + CF + LC) / (2*B + EL)
             = (righe coperte + branch coperti) / (righe eseguibili + branch totali)

Uso:
    sonar_coverage.py jacoco.xml [altro.xml ...] [--threshold 80] [--exclusions "a,b"]
                      [--diff-base origin/main --repo-root .] [--top 10] [--json]
"""
import argparse
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


def glob_to_regex(pattern):
    """Converte un pattern stile Sonar/Ant (**, *, ?) in regex."""
    pattern = pattern.strip().replace("\\", "/")
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("(?:^|.*/)" + out + "$")


def is_excluded(path, patterns):
    return any(p.match(path) for p in patterns)


def parse_reports(paths):
    """Ritorna {percorso_sorgente: {riga: (mi, ci, mb, cb)}} (es. com/acme/Foo.java)."""
    files = defaultdict(dict)
    for report in paths:
        root = ET.parse(report).getroot()
        for pkg in root.iter("package"):
            pkg_name = pkg.get("name", "")
            for sf in pkg.findall("sourcefile"):
                path = f"{pkg_name}/{sf.get('name')}" if pkg_name else sf.get("name")
                lines = files[path]
                for ln in sf.findall("line"):
                    nr = int(ln.get("nr"))
                    vals = tuple(int(ln.get(k, 0)) for k in ("mi", "ci", "mb", "cb"))
                    if nr in lines:  # report multipli: unisci prendendo il massimo coperto
                        old = lines[nr]
                        tot_b = max(old[2] + old[3], vals[2] + vals[3])
                        cb = max(old[3], vals[3])
                        ci = max(old[1], vals[1])
                        vals = (0 if ci else max(old[0], vals[0]), ci, tot_b - cb, cb)
                    lines[nr] = vals
    return files


def changed_lines(diff_base, repo_root):
    """Ritorna {percorso_repo: set(righe aggiunte/modificate)} rispetto a diff_base."""
    merge_base = subprocess.run(
        ["git", "-C", repo_root, "merge-base", diff_base, "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    diff = subprocess.run(
        ["git", "-C", repo_root, "diff", "-U0", "--no-color", merge_base, "--"],
        capture_output=True, text=True, check=True).stdout
    # include anche le modifiche non committate (git diff merge_base confronta col working tree)
    result, current = defaultdict(set), None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = None if line[4:] == "/dev/null" else line[4:].removeprefix("b/")
        elif line.startswith("@@") and current:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, count = int(m.group(1)), int(m.group(2) or 1)
            result[current].update(range(start, start + count))
    return result


def stats(lines, only=None):
    el = lc = b = cb = 0
    uncovered = []
    for nr, (mi, ci, mb, cbr) in lines.items():
        if only is not None and nr not in only:
            continue
        if mi + ci > 0:
            el += 1
            if ci > 0:
                lc += 1
            else:
                uncovered.append(nr)
        b += mb + cbr
        cb += cbr
        if mb > 0 and ci > 0:
            uncovered.append(nr)  # riga coperta ma con condizioni non coperte
    return {"el": el, "lc": lc, "b": b, "cb": cb, "uncovered": sorted(set(uncovered))}


def summarize(s, threshold):
    total, covered = s["el"] + s["b"], s["lc"] + s["cb"]
    cov = 100.0 * covered / total if total else None
    needed = max(0, math.ceil(threshold / 100.0 * total - covered)) if total else 0
    return {
        "coverage": round(cov, 1) if cov is not None else None,
        "line_coverage": round(100.0 * s["lc"] / s["el"], 1) if s["el"] else None,
        "condition_coverage": round(100.0 * s["cb"] / s["b"], 1) if s["b"] else None,
        "lines_to_cover": s["el"], "uncovered_lines": s["el"] - s["lc"],
        "conditions_to_cover": s["b"], "uncovered_conditions": s["b"] - s["cb"],
        "elements_needed_for_threshold": needed,
        "passed": cov is None or cov >= threshold,
    }


def add(acc, s):
    for k in ("el", "lc", "b", "cb"):
        acc[k] += s[k]


def compress(nums, limit=15):
    ranges, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            ranges.append((start, prev))
            start = prev = n
    if start is not None:
        ranges.append((start, prev))
    txt = [f"{a}-{b}" if a != b else str(a) for a, b in ranges[:limit]]
    return ", ".join(txt) + (" …" if len(ranges) > limit else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--threshold", type=float, default=80.0)
    ap.add_argument("--exclusions", default="")
    ap.add_argument("--diff-base")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    patterns = [glob_to_regex(p) for p in args.exclusions.split(",") if p.strip()]
    files = parse_reports(args.reports)
    diff = changed_lines(args.diff_base, args.repo_root) if args.diff_base else None

    overall, new = defaultdict(int), defaultdict(int)
    per_file, excluded = [], []
    for path, lines in sorted(files.items()):
        repo_path = None
        if diff is not None:
            repo_path = next((p for p in diff if p.endswith("/" + path) or p == path), None)
        if is_excluded(path, patterns) or (repo_path and is_excluded(repo_path, patterns)):
            excluded.append(path)
            continue
        s = stats(lines)
        add(overall, s)
        entry = {"file": path, **summarize(s, args.threshold),
                 "uncovered_at": compress(s["uncovered"])}
        if repo_path:
            sn = stats(lines, diff[repo_path])
            add(new, sn)
            if sn["el"] + sn["b"]:
                entry["new_code"] = {**summarize(sn, args.threshold),
                                     "uncovered_at": compress(sn["uncovered"])}
        per_file.append(entry)

    worst = sorted(per_file, key=lambda e: -(e["uncovered_lines"] + e["uncovered_conditions"]))
    result = {
        "threshold": args.threshold,
        "overall": summarize(overall, args.threshold),
        "new_code": summarize(new, args.threshold) if diff is not None else None,
        "excluded_files": excluded,
        "worst_files": [e for e in worst[:args.top]
                        if e["uncovered_lines"] + e["uncovered_conditions"]],
    }

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    def show(title, r):
        if r is None:
            return
        if r["coverage"] is None:
            print(f"{title}: nessuna riga da coprire")
            return
        esito = "PASS" if r["passed"] else "FAIL"
        print(f"{title}: {r['coverage']}%  [{esito}, soglia {args.threshold}%]")
        print(f"  line coverage      {r['line_coverage']}%  "
              f"({r['uncovered_lines']}/{r['lines_to_cover']} righe scoperte)")
        print(f"  condition coverage {r['condition_coverage']}%  "
              f"({r['uncovered_conditions']}/{r['conditions_to_cover']} condizioni scoperte)")
        if not r["passed"]:
            print(f"  servono altri {r['elements_needed_for_threshold']} elementi coperti "
                  f"(righe o condizioni) per raggiungere la soglia")

    show("OVERALL", result["overall"])
    show("NEW CODE", result["new_code"])
    if excluded:
        print(f"\nFile esclusi da Sonar: {len(excluded)}")
    print("\nFile con più elementi scoperti:")
    for e in result["worst_files"]:
        print(f"  {e['coverage']:>5}%  {e['file']}  "
              f"(righe scoperte {e['uncovered_lines']}, condizioni {e['uncovered_conditions']})"
              f"  righe: {e['uncovered_at']}")
    sys.exit(0 if (result["new_code"] or result["overall"])["passed"] else 1)


if __name__ == "__main__":
    main()
