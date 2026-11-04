#!/usr/bin/env python3
# *grabs a stopwatch and a clipboard* OwO eval/run_metrics.py MEASURES VALOR's success metrics! ^w^
# For each spec, several cycles of: destroy, plan, build + verify (timed), build AGAIN (must change nothing), plan
# again (must be all "keep"). Plans of all cycles are compared too (does a rebuild give exactly the same range?).
# Results go to eval/results/ as JSON and Markdown *wags tail*
"""Measure the MVP success metrics (spec section 11) for one or more range specs.

For each spec and each cycle: destroy -> plan -> apply --verify (timed) -> second apply (must change nothing)
-> plan (must be all 'keep'). Plans of all cycles are compared (rebuild fidelity).

Usage, as root inside the VALOR VM (copy this file in; the specs are names in /var/lib/valor/data/ranges):
    python3 run_metrics.py web2tier.yaml [pentest.yaml ...] --cycles 3
Results: results/<timestamp>.json and results/<timestamp>.md next to this file
"""

# Lazy type hints UwU
from __future__ import annotations

# argparse, json, subprocess (runs the valor CLI), time, Path >w<
import argparse
import json
import subprocess
import time
from pathlib import Path

# VALOR = the CLI with JSON output; OUT = the results folder OwO
VALOR = ["/opt/valor/bin/valor", "--json"]      # runs the engine as the valor user
OUT = Path(__file__).resolve().parent / "results"


# valor(): run one CLI command; returns (exit code, parsed JSON) (or the raw output when it isn't JSON) ^w^
def valor(*args: str) -> tuple[int, dict]:
    p = subprocess.run(VALOR + list(args), capture_output=True, text=True)
    try:
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        return p.returncode, {"raw": p.stdout[-2000:], "stderr": p.stderr[-2000:]}


# normalized_plan(): just the fields that matter for comparing plans across rebuilds UwU
def normalized_plan(plan: dict) -> list:
    keys = ("host", "action", "role", "os", "segment", "address", "cores", "memory_mib", "disk_gib", "services")
    return [{k: a.get(k) for k in keys} for a in plan.get("actions", [])] + [plan.get("totals")]


# run(): all cycles for one spec >w<
def run(spec: str, cycles: int) -> dict:
    name = Path(spec).stem
    rows, plans = [], []
    for c in range(1, cycles + 1):
        # Start from nothing, plan, then build + verify against the clock OwO
        valor("destroy", name, "--yes")
        _, plan = valor("plan", spec)
        plans.append(normalized_plan(plan))
        t0 = time.time()
        code, res = valor("apply", spec, "--verify")
        build = round(time.time() - t0, 1)
        # The verification's counts ^w^
        v = (res.get("verify") or {}).get("summary", {})
        # A second build must change nothing; the plan after it must be all "keep" UwU
        t1 = time.time()
        _, res2 = valor("apply", spec)
        second = round(time.time() - t1, 1)
        _, plan2 = valor("plan", spec)
        # One result row per cycle, printed as it finishes >w<
        rows.append({
            "cycle": c, "exit": code, "ok": res.get("ok"), "build_and_verify_s": build,
            "tests": f"{v.get('tests_passed')}/{v.get('tests_total')}",
            "isolation": f"{v.get('isolation_passed')}/{v.get('isolation_total')}",
            "baseline": f"{v.get('baseline_passed')}/{v.get('baseline_total')}",
            "second_apply_changed": res2.get("changed"), "second_apply_s": second,
            "plan_after_all_keep": set(plan2.get("summary", {})) == {"keep"},
            "error": res.get("error"), "message": res.get("message"),
        })
        print(json.dumps(rows[-1]), flush=True)
    # identical_plans: did every cycle plan exactly the same thing? OwO
    return {"spec": spec, "cycles": rows, "identical_plans": all(p == plans[0] for p in plans)}


# main(): parse the arguments, run every spec, tear down (unless --keep), and write the JSON and a Markdown table ^w^
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--keep", action="store_true", help="leave the last build running")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    results = [run(s, a.cycles) for s in a.specs]
    if not a.keep:
        for s in a.specs:
            valor("destroy", Path(s).stem, "--yes")
    (OUT / f"{stamp}.json").write_text(json.dumps(results, indent=2))
    md = [f"# VALOR metrics run {stamp}", ""]
    for r in results:
        cyc = r["cycles"]
        md += [f"## {r['spec']}", "",
               "| Cycle | Result | Build + verify | Tests | Isolation | Baseline | 2nd apply changes | 2nd apply time | Plan after = keep |",
               "|---|---|---|---|---|---|---|---|---|"]
        for x in cyc:
            md.append(f"| {x['cycle']} | {'PASS' if x['ok'] else 'FAIL ' + str(x['error'])} | {x['build_and_verify_s']} s | "
                      f"{x['tests']} | {x['isolation']} | {x['baseline']} | {x['second_apply_changed']} | "
                      f"{x['second_apply_s']} s | {x['plan_after_all_keep']} |")
        md += ["", f"Identical plans across rebuilds: **{r['identical_plans']}**", ""]
    (OUT / f"{stamp}.md").write_text("\n".join(md) + "\n")
    print(f"results: {OUT / (stamp + '.md')}")


# Run main() when started as a script UwU *stops the stopwatch*
if __name__ == "__main__":
    main()
