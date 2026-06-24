"""
verifier_calibration.py — is the inline Verifier too strict or too lax?

For each troubleshoot_case: run the diagnosis chain, then score it TWO ways — the
inline Verifier (Gemini, the one in the workflow) and an independent offline judge
(openevals faithfulness). Build a confusion matrix:

    inline APPROVE + offline grounded    -> true accept
    inline REJECT  + offline ungrounded  -> true reject
    inline REJECT  + offline grounded    -> FALSE REJECT (too strict)
    inline APPROVE + offline ungrounded  -> FALSE ACCEPT (too lax)

and recommend a calibration (approve when verdict score >= threshold). Report only —
apply via agents/nodes/verifier.py + config after review (record in TUNING_LOG.md).

Heavy: needs the HTTP MCP server up (diagnosis tools) + Groq + Gemini + the eval judge.
    python mcp_server/server.py http        # separate terminal
    python eval/tuning/verifier_calibration.py
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for p in ("agents", "eval", "eval/evaluators"):
    sys.path.insert(0, str(ROOT / p))

CASES = ROOT / "eval" / "datasets" / "troubleshoot_cases.jsonl"
OUT = ROOT / "eval" / "results" / "tuning"
FAITH_THRESHOLD = 0.7   # offline "grounded" cutoff


def _ctx(rc):
    parts = (rc or {}).get("manual", []) + (rc or {}).get("safety", [])
    return "\n\n".join((c.get("text") or "") for c in parts)


async def run():
    from nodes.diagnosis import diagnosis_node
    from nodes.verifier import verifier_node
    from llm_judges import faithfulness

    rows = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]
    fr, fa, tr, ta, skipped = 0, 0, 0, 0, 0
    detail = []
    for ex in rows:
        inp, ref = ex["inputs"], ex["reference"]
        if ref.get("expect_low_confidence") or ref.get("expect_flagged"):
            skipped += 1
            continue
        state = {"machine_id": inp["machine_id"], "mvc_code": inp["mvc_code"], "symptom": inp["symptom"]}
        d = await diagnosis_node(state)
        state.update(d)
        verdict = verifier_node(state)["verdict"]
        inline_ok = bool(verdict.get("approved"))

        ctx = _ctx(d.get("retrieved_context"))
        dx = d.get("diagnosis") or {}
        diag_text = (f"root_cause: {dx.get('root_cause')}\nfix_steps: {dx.get('fix_steps')}\n"
                     f"needs_technician: {dx.get('needs_technician')}\nsafety_notes: {dx.get('safety_notes')}")
        run_obj = SimpleNamespace(outputs={"context_text": ctx, "diagnosis_text": diag_text})
        ex_obj = SimpleNamespace(inputs={"symptom": inp["symptom"]}, outputs={})
        fres = faithfulness(run_obj, ex_obj)
        score = fres.get("score")
        offline_ok = (score is not None) and (score >= FAITH_THRESHOLD)

        if inline_ok and offline_ok:
            ta += 1; verdict_label = "true_accept"
        elif not inline_ok and not offline_ok:
            tr += 1; verdict_label = "true_reject"
        elif not inline_ok and offline_ok:
            fr += 1; verdict_label = "FALSE_REJECT"
        else:
            fa += 1; verdict_label = "FALSE_ACCEPT"
        detail.append({"case_id": ex["id"], "inline_approved": inline_ok,
                       "inline_score": verdict.get("score"), "offline_faithfulness": score,
                       "verdict": verdict_label})
        print(f"  {ex['id']:28} inline={inline_ok!s:5} score={verdict.get('score')} "
              f"offline={score}  -> {verdict_label}")

    print(f"\nConfusion: true_accept={ta} true_reject={tr} FALSE_REJECT={fr} FALSE_ACCEPT={fa} (skipped {skipped})")
    if fr and not fa:
        print("Recommendation: Verifier is TOO STRICT — soften it (approve when verdict score >= 3 of 5, or relax the prompt).")
    elif fa and not fr:
        print("Recommendation: Verifier is TOO LAX — tighten it (raise approval bar / sharpen the prompt).")
    elif fr and fa:
        print("Recommendation: mixed errors — calibrate the score threshold; inspect the FALSE_* cases.")
    else:
        print("Recommendation: Verifier is well-calibrated on this set — no change.")
    print("Report only — apply via verifier.py/config after review (record in TUNING_LOG.md).")

    OUT.mkdir(parents=True, exist_ok=True)
    import openpyxl
    from openpyxl.styles import Font
    path = OUT / f"verifier_calibration_{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "verifier_calibration"
    cols = ["case_id", "inline_approved", "inline_score", "offline_faithfulness", "verdict"]
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    for d in detail:
        ws.append([d[c] for c in cols])
    wb.save(path)
    print(f"\nExcel: {path}")


if __name__ == "__main__":
    asyncio.run(run())
