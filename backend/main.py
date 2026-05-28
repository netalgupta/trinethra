"""
Trinethra Supervisor Feedback Analyzer — v3 "The Deliberation Build"
DeepThought Software Developer Assignment

Architecture:
  POST /api/analyze/stream  — SSE stream, returns evidence/gaps/questions (NO score until deliberation)
  POST /api/deliberate      — Takes intern answers + AI analysis, returns deliberation score + explanation
  GET  /api/samples         — Sample transcripts
  GET  /api/health          — Ollama status
"""

import json
import re
import time
import logging
from pathlib import Path
from typing import Optional, Generator

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Trinethra v3", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

OLLAMA_BASE  = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2"
TIMEOUT      = 360

BASE_DIR = Path(__file__).parent.parent
with open(BASE_DIR / "rubric.json")             as f: RUBRIC  = json.load(f)
with open(BASE_DIR / "sample-transcripts.json") as f: SAMPLES = json.load(f)


# ── Pydantic models ──────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    transcript:      str
    fellow_name:     Optional[str] = "the Fellow"
    client_name:     Optional[str] = "the client"
    supervisor_name: Optional[str] = "the supervisor"
    model:           Optional[str] = None

class DeliberateRequest(BaseModel):
    analysis:    dict            # full AI analysis dict
    q1:          str             # problem_id: yes | possibly | no
    q2:          str             # survivability: keeps_running | mixed | everything_stops
    q3:          list            # bias flags selected by intern
    q4:          int             # gut score 1-10
    fellow_name: Optional[str] = "the Fellow"


# ── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM = """You are a senior assessment specialist at DeepThought, a B2B consulting firm. You analyze supervisor transcripts about Fellows placed in Indian manufacturing companies and produce structured, evidence-grounded assessments.

PRINCIPLES:
1. Every finding must be grounded in VERBATIM quotes from the transcript. Never fabricate evidence.
2. Distinguish Layer 1 (execution) from Layer 2 (systems building) for every evidence item.
3. Apply the 6 vs 7 boundary precisely — this is the most important scoring decision.
4. Actively detect supervisor biases. Never let supervisor warmth inflate the score.
5. Output ONLY valid JSON. No preamble, no markdown, no commentary outside the JSON.

CRITICAL 6 vs 7 BOUNDARY:
Score 6: Supervisor defines the work. Fellow executes reliably.
Score 7: Fellow identifies problems the supervisor HADN'T ASKED ABOUT, independently.
Ask: who first defined the scope of this task — supervisor or Fellow?

LAYER MODEL:
Layer 1 = Execution: meetings, tracking, coordination, calls, reports. Necessary but not the mandate.
Layer 2 = Systems: SOPs, trackers, dashboards, processes that RUN WITHOUT THE FELLOW.
SURVIVABILITY TEST: If the Fellow left tomorrow, would anything they built continue without them?

KPI MAP (supervisor uses plain language — map it):
Lead Generation, Lead Conversion, Upselling, Cross-selling, NPS, PAT, TAT, Quality

ASSESSMENT DIMENSIONS:
driving_execution | systems_building | kpi_impact | change_management

SUPERVISOR BIASES:
helpfulness_bias: "handles all my calls" sounds like 8, is actually 5-6 (task absorption)
presence_bias: floor presence rated higher than desk/laptop systems work
halo_horn_effect: one big story colors everything
recency_bias: last 2 weeks dominate
dependency_trap: Fellow absorbed founder's workload — crutch, not builder"""


def build_prompt(transcript: str, fellow: str, client: str, supervisor: str) -> str:
    return f"""{SYSTEM}

Fellow: {fellow} | Client: {client} | Supervisor: {supervisor}

TRANSCRIPT:
---
{transcript}
---

Return ONLY this JSON object. Every field required.

{{
  "score": {{
    "value": <int 1-10>,
    "label": "<rubric label>",
    "band": "<Need Attention|Productivity|Performance>",
    "justification": "<3-4 sentences. State which layer dominates. Explicitly address the 6/7 boundary. Cite verbatim evidence.>",
    "confidence": "<low|medium|high>",
    "confidence_reason": "<what's missing or ambiguous>"
  }},
  "evidence": [
    {{
      "quote": "<EXACT verbatim quote from transcript>",
      "signal": "<positive|negative|neutral>",
      "dimension": "<driving_execution|systems_building|kpi_impact|change_management>",
      "layer": "<layer_1|layer_2>",
      "interpretation": "<1-2 sentences: what this proves AND what it does not prove. Be skeptical.>"
    }}
  ],
  "kpi_mapping": [
    {{
      "kpi": "<kpi name>",
      "evidence": "<transcript phrase>",
      "system_or_personal": "<system|personal>",
      "note": "<sustainable if Fellow leaves?>"
    }}
  ],
  "gaps": [
    {{
      "dimension": "<dimension id>",
      "severity": "<critical|moderate|minor>",
      "detail": "<specific absence — what the supervisor DID NOT mention>"
    }}
  ],
  "follow_up_questions": [
    {{
      "question": "<conversational question for next call>",
      "target_gap": "<dimension id>",
      "looking_for": "<what strong vs weak answer looks like>"
    }}
  ],
  "bias_flags": [
    {{
      "bias_type": "<bias name>",
      "evidence_from_transcript": "<triggering quote or paraphrase>",
      "score_impact": "<how this inflates or deflates score and by how much>"
    }}
  ]
}}

Rules:
- evidence: 5-8 quotes minimum, verbatim, mix positive and negative signals
- kpi_mapping: only evidenced KPIs, empty array if none
- gaps: check all 4 dimensions, flag absences not just negatives
- follow_up_questions: 3-5, conversational tone, each targets a specific gap
- bias_flags: every detected bias; empty array if none
- Do NOT let supervisor warmth inflate the score. Score the behavioral evidence."""


DELIBERATION_SYSTEM = """You are a calibration advisor helping a psychology intern finalize a Fellow assessment score. You receive:
1. The AI's draft analysis and proposed score
2. The intern's deliberation answers (4 structured questions)
3. A computed deliberation score (calculated algorithmically)

Your job is to write a short, specific calibration note (3-5 sentences) that:
- Explains why the deliberation score diverges from the AI score (if it does)
- Points to the 1-2 most important evidence items the intern should re-read
- Names the specific boundary or test that drove the divergence (6/7 boundary, survivability test, or a bias)
- Does NOT tell the intern what score to choose — they decide

Output ONLY a JSON object: {{"calibration_note": "<your 3-5 sentence note>", "key_evidence_indices": [<0-based indices of most important evidence items>]}}"""


def build_deliberation_prompt(analysis: dict, q1: str, q2: str, q3: list,
                               q4: int, delib_score: int, fellow: str) -> str:
    ai_score   = analysis.get("score", {}).get("value", 5)
    divergence = abs(delib_score - ai_score)
    ev_summary = "\n".join([
        f"  [{i}] ({e.get('layer','?')}/{e.get('signal','?')}) \"{e.get('quote','')[:80]}…\""
        for i, e in enumerate(analysis.get("evidence", [])[:8])
    ])
    return f"""AI score: {ai_score} | Deliberation score: {delib_score} | Divergence: {divergence} points

Fellow: {fellow}
AI justification: {analysis.get('score',{}).get('justification','')}

Intern deliberation answers:
  Q1 (Problem identification): {q1}
  Q2 (Survivability): {q2}
  Q3 (Bias flags selected): {', '.join(q3) if q3 else 'none'}
  Q4 (Gut score): {q4}

Evidence summary (first 8):
{ev_summary}

Detected bias flags: {json.dumps([b.get('bias_type') for b in analysis.get('bias_flags', [])])}

Write your calibration note. Output ONLY the JSON object."""


# ── Ollama helpers ────────────────────────────────────────────────────────────

def check_ollama() -> dict:
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        if r.status_code == 200:
            return {"status": "connected", "models": [m["name"] for m in r.json().get("models", [])]}
    except Exception:
        pass
    return {"status": "offline", "models": []}


def call_ollama(prompt: str, model: str, stream: bool = False):
    payload = {
        "model": model, "prompt": prompt, "stream": stream,
        "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 4096}
    }
    return requests.post(f"{OLLAMA_BASE}/api/generate", json=payload,
                         timeout=TIMEOUT, stream=stream)


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    for fn in [
        lambda s: json.loads(s),
        lambda s: json.loads(s[s.find("{"):s.rfind("}")+1]),
        lambda s: json.loads(re.sub(r"```(?:json)?", "", s).strip()),
    ]:
        try: return fn(raw)
        except: continue
    raise ValueError(f"JSON parse failed. Excerpt: {raw[:300]}")


def repair(data: dict) -> dict:
    for f in ["evidence","kpi_mapping","gaps","follow_up_questions","bias_flags"]:
        if f not in data or not isinstance(data[f], list): data[f] = []
    if "score" not in data:
        data["score"] = {"value":0,"label":"Parse error","band":"Unknown",
                          "justification":"Model response could not be parsed.",
                          "confidence":"low","confidence_reason":"Parse failure"}
    return data


# ── Deliberation score algorithm ──────────────────────────────────────────────

def compute_deliberation_score(ai_score: int, q1: str, q2: str,
                                q3: list, q4: int) -> tuple[int, list]:
    score    = float(ai_score)
    reasons  = []

    # Q1: 6/7 boundary
    if q1 == "yes":
        if score < 7: score = max(score, 7.0); reasons.append("Intern confirmed independent problem identification → score floor raised to 7")
    elif q1 == "no":
        if score > 6: score = min(score, 6.0); reasons.append("No independent problem identification found → score ceiling capped at 6")

    # Q2: survivability
    if q2 == "keeps_running":
        if score < 7: score = max(score, 7.0); reasons.append("Survivability test passed (systems continue without Fellow) → score floor raised to 7")
    elif q2 == "everything_stops":
        if score > 6: score = min(score, 6.0); reasons.append("Survivability test failed (everything depends on Fellow's personal presence) → score ceiling capped at 6")

    # Q3: bias corrections
    if "helpfulness_bias" in q3:
        score -= 1.0; reasons.append("Helpfulness bias detected → score adjusted −1 (supervisor happy = Fellow doing their work, not building systems)")
    if "presence_bias" in q3:
        score += 0.5; reasons.append("Presence bias detected → score adjusted +0.5 (systems work done at desk may be undervalued by supervisor)")
    if "halo_positive" in q3:
        score -= 0.75; reasons.append("Positive halo effect detected → score adjusted −0.75 (one strong story may be inflating overall assessment)")
    if "halo_negative" in q3:
        score += 0.75; reasons.append("Negative halo effect detected → score adjusted +0.75 (one weak area may be deflating overall assessment)")
    if "dependency_trap" in q3:
        score -= 1.0; reasons.append("Dependency trap detected → score adjusted −1 (Fellow absorbing founder's workload, not building lasting systems)")

    # Q4: blend with gut score (35% gut weight)
    blended = score * 0.65 + q4 * 0.35
    if abs(blended - score) > 0.5:
        reasons.append(f"Gut score ({q4}) blended in at 35% weight")

    final = max(1, min(10, round(blended)))
    return final, reasons


# ── SSE streaming ─────────────────────────────────────────────────────────────

def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data) if not isinstance(data,str) else data}\n\n"


def stream_analysis(req: AnalyzeRequest) -> Generator[str, None, None]:
    model    = req.model or OLLAMA_MODEL
    fellow   = req.fellow_name or "the Fellow"
    client   = req.client_name or "the client"
    sup      = req.supervisor_name or "the supervisor"

    yield sse("status", {"phase":"start","message":"Connecting to Ollama…"})

    if check_ollama()["status"] != "connected":
        yield sse("error", {"message":"Ollama is not running. Start it with: ollama serve"}); return

    yield sse("status", {"phase":"prompting","message":f"Sending transcript to {model}…"})
    prompt = build_prompt(req.transcript, fellow, client, sup)
    log.info(f"Analyzing: model={model} words={len(req.transcript.split())}")

    try:
        resp = call_ollama(prompt, model, stream=True)
        resp.raise_for_status()
    except Exception as e:
        yield sse("error", {"message":f"Ollama call failed: {e}"}); return

    full, toks = "", 0
    yield sse("status", {"phase":"generating","message":"Model is analyzing…"})

    for line in resp.iter_lines():
        if not line: continue
        try:
            chunk = json.loads(line)
            full += chunk.get("response","")
            toks += 1
            if toks % 60 == 0:
                yield sse("progress", {"tokens":toks})
            if chunk.get("done"): break
        except: continue

    yield sse("status", {"phase":"parsing","message":"Parsing structured output…"})

    try:
        parsed = extract_json(full)
    except ValueError:
        yield sse("status", {"phase":"retry","message":"Retrying with stricter prompt…"})
        try:
            r2   = call_ollama(prompt + "\n\nOutput ONLY the JSON. Start with { end with }.", model)
            r2.raise_for_status()
            parsed = extract_json(r2.json()["response"])
        except Exception as e:
            yield sse("error", {"message":f"Parse failed after retry: {e}"}); return

    analysis = repair(parsed)
    analysis["meta"] = {
        "fellow_name": fellow, "client": client, "supervisor": sup,
        "model": model, "transcript_words": len(req.transcript.split()),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    yield sse("status", {"phase":"done","message":"Analysis ready"})
    yield sse("result", analysis)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    s = check_ollama()
    return {"backend":"ok","ollama":s["status"],"models":s["models"]}


@app.get("/api/samples")
def samples():
    return [{
        "id":             s["id"],
        "label":          f"{s['fellow']['name']} @ {s['company']['name']}",
        "fellow_name":    s["fellow"]["name"],
        "client_name":    s["company"]["name"],
        "supervisor_name":s["supervisor"]["name"],
        "transcript":     s["transcript"],
        "expected_score_range": s.get("expectedScoreRange"),
        "context":        s["company"]["context"],
    } for s in SAMPLES]


@app.post("/api/analyze/stream")
async def analyze_stream(req: AnalyzeRequest):
    if not req.transcript.strip():
        raise HTTPException(400, "Transcript cannot be empty.")
    if len(req.transcript.split()) < 30:
        raise HTTPException(400, "Transcript too short — paste the full supervisor feedback.")
    return StreamingResponse(stream_analysis(req), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.post("/api/deliberate")
def deliberate(req: DeliberateRequest):
    """
    Compute the deliberation-adjusted score and generate a calibration note.
    This is intentionally synchronous and fast — the algorithm is deterministic,
    and the LLM call only happens to generate the calibration text.
    """
    ai_score = req.analysis.get("score", {}).get("value", 5)
    delib_score, reasons = compute_deliberation_score(ai_score, req.q1, req.q2, req.q3, req.q4)
    divergence = abs(delib_score - ai_score)

    calibration_note = ""
    key_indices      = []

    if divergence >= 2:
        # Only fire LLM if there's meaningful divergence — saves time
        if check_ollama()["status"] == "connected":
            try:
                model  = OLLAMA_MODEL
                prompt = f"{DELIBERATION_SYSTEM}\n\n{build_deliberation_prompt(req.analysis, req.q1, req.q2, req.q3, req.q4, delib_score, req.fellow_name or 'the Fellow')}"
                r      = call_ollama(prompt, model)
                r.raise_for_status()
                d = extract_json(r.json()["response"])
                calibration_note = d.get("calibration_note", "")
                key_indices      = d.get("key_evidence_indices", [])
            except Exception as e:
                log.warning(f"Deliberation LLM call failed: {e}")
                calibration_note = (
                    f"Your deliberation suggests {delib_score}, the AI suggested {ai_score}. "
                    f"Key reasons for the difference: {'; '.join(reasons[:2])}. "
                    "Review the evidence carefully before finalizing."
                )

    return {
        "deliberation_score":  delib_score,
        "ai_score":            ai_score,
        "divergence":          divergence,
        "reasons":             reasons,
        "calibration_note":    calibration_note,
        "key_evidence_indices": key_indices,
    }


# ── Static frontend ───────────────────────────────────────────────────────────
fe_dir = BASE_DIR / "frontend"
if fe_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(fe_dir)), name="static")

    @app.get("/")
    @app.get("/{path:path}")
    def serve(path: str = ""):
        return FileResponse(str(fe_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
