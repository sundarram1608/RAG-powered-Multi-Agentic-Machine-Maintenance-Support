"""
diagnosis_sweep.py — do extra corrective-RAG retries earn their cost?

Sweeps MAX_DIAGNOSIS_REQUERIES in {1,2,3} (overridden in-process, never written) and,
for each troubleshoot_case, scores the diagnosis with the offline judge (faithfulness
+ answer relevance) and the gate check, plus mean latency. Shows whether 3 retries
beat 1-2 on grounding, and at what time cost.

Heavy: needs the HTTP MCP server up + Groq + Gemini + the eval judge.
    python mcp_server/server.py http        # separate terminal
    python eval/tuning/diagnosis_sweep.py

Report only — apply via agents/config.py after review (record in TUNING_LOG.md).
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for p in ("agents", "eval", "eval/evaluators"):
    sys.path.insert(0, str(ROOT / p))

CASES = ROOT / "eval" / "datasets" / "troubleshoot_cases.jsonl"
OUT = ROOT / "eval" / "results" / "tuning"
CAPS = [1, 2, 3]


def _diag_text(dx):
    return (f"root_cause: {dx.get('root_cause')}\nfix_steps: {dx.get('fix_steps')}\n"
            f"needs_technician: {dx.get('needs_technician')}\nsafety_notes: {dx.get('safety_notes')}")


def _ctx(rc):
    parts = (rc or {}).get("manual", []) + (rc or {}).get("safety", [])
    return "\n\n".join((c.get("text") or "") for c in parts)


async def run():
    import config
    from nodes.diagnosis import diagnosis_node
    from llm_judges import answer_relevance, faithfulness

    rows = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()
            if not json.loads(l)["reference"].get("expect_low_confidence")]
    results = []
    for cap in CAPS:
        config.MAX_DIAGNOSIS_REQUERIES = cap     # read live by diagnosis_node
        f_sum, r_sum, g_ok, n = 0.0, 0.0, 0, 0
        t0 = time.perf_counter()
        for ex in rows:
            inp, ref = ex["inputs"], ex["reference"]
            state = {"machine_id": inp["machine_id"], "mvc_code": inp["mvc_code"], "symptom": inp["symptom"]}
            d = await diagnosis_node(state)
            dx = d.get("diagnosis") or {}
            run_obj = SimpleNamespace(outputs={"context_text": _ctx(d.get("retrieved_context")),
                                               "diagnosis_text": _diag_text(dx)})
            ex_obj = SimpleNamespace(inputs={"symptom": inp["symptom"]}, outputs={})
            fs = faithfulness(run_obj, ex_obj).get("score")
            rs = answer_relevance(run_obj, ex_obj).get("score")
            if fs is not None:
                f_sum += fs; n += 1
            if rs is not None:
                r_sum += rs
            if ref.get("needs_technician") is not None and dx.get("needs_technician") == ref["needs_technician"]:
                g_ok += 1
        lat = (time.perf_counter() - t0) / len(rows)
        rec = {"requery_cap": cap, "faithfulness": f_sum / n if n else 0.0,
               "answer_relevance": r_sum / n if n else 0.0,
               "gate_acc": g_ok / len(rows), "latency_s": lat}
        results.append(rec)
        print(f"  cap={cap}  faithfulness={rec['faithfulness']:.2f} "
              f"answer_relevance={rec['answer_relevance']:.2f} gate={rec['gate_acc']:.2f}  {lat:.1f}s/case")

    print("\nReport only — pick the sweet spot (quality vs latency) and set "
          "config.MAX_DIAGNOSIS_REQUERIES after review (record in TUNING_LOG.md).")
    OUT.mkdir(parents=True, exist_ok=True)
    import openpyxl
    from openpyxl.styles import Font
    path = OUT / f"diagnosis_sweep_{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "diagnosis_sweep"
    cols = ["requery_cap", "faithfulness", "answer_relevance", "gate_acc", "latency_s"]
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    for rec in results:
        ws.append([rec["requery_cap"]] + [round(rec[c], 3) for c in cols[1:]])
    wb.save(path)
    print(f"Excel: {path}")


if __name__ == "__main__":
    asyncio.run(run())
