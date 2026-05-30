# Trinethra — Supervisor Feedback Analyzer
### DeepThought Software Developer Assignment

> *"The AI suggests. The intern decides."*

---

## What this is

A web tool that helps DeepThought psychology interns analyze supervisor feedback about Fellows. It uses a local Ollama LLM to extract evidence, identify gaps, detect supervisor biases, and suggest a score — but shows that score only **after** the intern completes a structured deliberation. This prevents automation bias at the source.

---

## Quick Start

### Prerequisites
- Python 3.9+
- [Ollama](https://ollama.com) installed

### 1. Pull the model
```bash
ollama pull llama3.2
```

### 2. Install Python dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 3. Start the app
```bash
python main.py
```

Open **http://localhost:8000** in your browser. No separate frontend server needed — FastAPI serves the HTML directly.

### One-liner (macOS/Linux)
```bash
bash start.sh
```

---

## Architecture

```
trinethra/
├── backend/
│   ├── main.py              # FastAPI — API routes, Ollama integration, prompt engine, deliberation algorithm
│   └── requirements.txt
├── frontend/
│   └── index.html           # Single-file UI — no build step, served by FastAPI
├── rubric.json              # 1-10 rubric as structured data, loaded at startup
├── sample-transcripts.json  # 3 test transcripts with expected score ranges
├── DECISIONS.md             # Product decisions log — what was cut, reversed, and why
├── start.sh                 # One-command launcher
└── README.md
```

**Request flow:**
```
Intern pastes transcript
  → POST /api/analyze/stream   (SSE)
  → FastAPI builds prompt (rubric + KPIs + bias heuristics embedded)
  → Ollama local LLM generates structured JSON
  → Frontend receives evidence, gaps, questions — score hidden
  → Intern reads evidence, forms own view
  → Intern answers 4 deliberation questions
  → POST /api/deliberate
  → Deliberation algorithm computes adjusted score
  → LLM writes calibration note (only if divergence ≥ 2 points)
  → AI score + deliberation score revealed side by side
  → Intern sets final score, generates exportable report
```

---

## The Core Design Decision: Score Hidden Until Deliberation

This is the thing that makes this tool different.

**The problem with showing the score immediately:** Once an intern sees "Score: 7," they anchor to it. They read all the evidence through the lens of justifying that number. The AI becomes the decision-maker; the intern becomes a rubber stamp.

**The fix — a 3-phase workflow:**

**Phase 1 — AI Draft:** Evidence, gaps, supervisor bias flags, and follow-up questions are shown. Score is locked behind a hidden card. Intern reads the full picture cold.

**Phase 2 — Deliberation:** Four plain-language questions the intern must answer before the score is revealed. Each question targets a specific failure mode: who initiated the work (6/7 boundary), what survives the Fellow's departure (survivability test), how the supervisor sounds (bias correction), and the intern's own read (gut score). The reveal button is disabled until all required questions are answered — this is intentional friction.

**Phase 3 — Score Reveal:** AI score and deliberation score appear side by side. If they diverge by 2+ points, a calibration warning identifies the specific evidence items driving the gap and explains why the two scores differ.

This directly solves **Challenge 4** from the assignment brief. See [DECISIONS.md](./DECISIONS.md) for why other approaches were rejected.

---

## Design Challenges Addressed

### Challenge 4 — Automation Bias (primary design constraint)
Three mechanisms working together:
1. **Score hidden until deliberation** — intern forms independent judgment first
2. **Deliberation score algorithm** — 4 questions compute an independent score from first principles, not just a feel-check
3. **Divergence warning with evidence links** — when |AI − deliberation| ≥ 2, specific evidence items are flagged and clickable

### Challenge 3 — Evidence Linking (solved)
Bidirectional: click an evidence card → that quote highlights in the Transcript tab. Click a highlighted quote → evidence card scrolls into view and pulses. Implemented with exact substring matching + 50-char fuzzy fallback (handles smart-quote normalization differences between LLM output and the original text).

### Challenge 5 — Gap Detection (addressed)
The prompt gives the model an explicit checklist of 4 assessment dimensions and instructs it to flag *absences* specifically — not just negatives. "No mention of change management" is treated differently from "supervisor says Fellow struggles with change management." A Layer 1/Layer 2 ratio bar in the UI gives the intern an instant visual proxy for the survivability question before deliberation begins.

### Challenge 2 — Structured Output Reliability (solved)
Three-layer JSON extraction: direct parse → extract between outermost braces → strip markdown fences. On failure: automatic retry with a stricter prompt addendum. On second failure: graceful error showing the raw excerpt so the intern can re-run. `repair()` fills missing sections with empty arrays so the UI never crashes on a partial response.

### Challenge 1 — One Prompt or Many (position: one)
One structured prompt per analysis. The five sections (score, evidence, KPIs, gaps, follow-up questions) are interdependent — follow-up questions should target the actual gaps found, not gaps in a vacuum. Multiple calls on a local CPU model would take 4–6 minutes total. Temperature 0.1 keeps output consistent. The deliberation calibration note is a separate lighter call — but only fires when divergence ≥ 2 points, so it doesn't add latency for the common case.

---

## Why llama3.2

- 3B parameters — runs on any laptop with 8GB RAM, no GPU needed
- Reliable instruction-following at this size for structured JSON output
- 45–90s per analysis on CPU — acceptable for a review workflow
- Can be swapped at runtime via the model dropdown — no code change

Alternative: `mistral` (7B) produces better output quality if you have 16GB RAM.

---

## The Deliberation Algorithm

```python
score = float(ai_score)

# Q1 — who first defined the scope of this work?
if intern: "clearly yes — Fellow identified problems independently":
    score = max(score, 7.0)   # floor raised: this is at least a 7
if intern: "no — everything was assigned by supervisor":
    score = min(score, 6.0)   # ceiling capped: this is at most a 6

# Q2 — survivability test
if intern: "things would keep running":
    score = max(score, 7.0)
if intern: "most things would stop":
    score = min(score, 6.0)

# Q3 — bias corrections
if helpfulness_bias selected:  score -= 1.0
if presence_bias selected:     score += 0.5   # systems work undervalued
if halo_positive selected:     score -= 0.75
if halo_negative selected:     score += 0.75
if dependency_trap selected:   score -= 1.0

# Q4 — blend intern gut score (35% weight)
final = score * 0.65 + gut_score * 0.35

# Clamp to 1–10
final = max(1, min(10, round(final)))
```

Q1 and Q2 act as hard gates on the 6/7 boundary — they can't be overridden by gut feel alone. Q3 corrects for known supervisor biases. Q4 gives the intern's judgment real weight without letting it dominate.

---

## Scoring the Three Sample Transcripts

The prompt is specifically engineered against the three "lazy scores" described in context.md:

| Fellow | Lazy score | Correct range | Key signal |
|---|---|---|---|
| Karthik | 8 | 6–7 | One Layer 2 signal (cycle time study). Everything else is Layer 1. Supervisor warmth inflates without bias correction. |
| Meena | 4 | 7–8 | Presence bias masks genuine systems work. Order tracker + dispatch risk alert are real Layer 2 contributions. |
| Anil | 9 | 5–6 | Dependency trap: Fellow absorbed founder's workload. Survivability test fails completely. Meeting structure is the only lasting system. |

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Backend and Ollama status, available models |
| GET | `/api/samples` | 3 test transcripts with metadata |
| POST | `/api/analyze/stream` | SSE stream — evidence, gaps, score, questions |
| POST | `/api/deliberate` | Deliberation score + calibration note |

**POST /api/analyze/stream**
```json
{
  "transcript": "...",
  "fellow_name": "Karthik",
  "client_name": "Veerabhadra Auto Components",
  "supervisor_name": "Mr. Suresh Patil",
  "model": "llama3.2"
}
```

**POST /api/deliberate**
```json
{
  "analysis": { "score": {...}, "evidence": [...], ... },
  "q1": "yes",
  "q2": "everything_stops",
  "q3": ["helpfulness_bias", "dependency_trap"],
  "q4": 6,
  "fellow_name": "Anil"
}
```

---

## What I'd Improve With More Time

1. **Streaming evidence cards** — render each evidence item the moment its JSON object is complete, rather than waiting for the full response. A partial JSON parser would detect when `evidence[n]` closes and stream it to the UI immediately. Right now there's a blank screen for 60–90 seconds before everything appears at once.

2. **Score trend view** — if the same Fellow has two or more transcripts across their tenure, show a score progression timeline. The intern can see trajectory (improving? plateauing?) rather than just a point-in-time snapshot.

3. **Editable evidence interpretations** — the intern can accept or reject each evidence item but can't edit the interpretation text. Adding inline editing would let them annotate their reasoning, which carries into the final report.

4. **Deliberation analytics dashboard** — aggregate deliberation data across all assessments over time: how often does the intern diverge from the AI by 2+ points? Which bias flags appear most? Which dimension gaps are most common by industry? This becomes calibration data for the next version of the prompt.

5. **Persistent session store** — SQLite-backed. Right now refreshing the page loses all work. A simple key-value store per assessment URL would fix this with minimal complexity.

---

## Assumptions Made

- Desktop-only — interns use this at a desk, not on mobile
- No authentication — internal single-user tool for each intern's session
- "10 minute target" in the brief refers to the intern's review time, not LLM generation time (45–90s on CPU is unavoidable with local models — the progress bar makes this feel less like waiting)
- Intern is comfortable running 3 terminal commands but is not a developer

---

## See Also

**[DECISIONS.md](./DECISIONS.md)** — explicit log of what was considered and cut, what was reversed mid-build, and where we disagreed with the AI's output. The assignment rubric asks "what tradeoffs did you make" — this file answers that directly.

---

## Commit History

```
May 28  09:14  init: project scaffold, .gitignore, requirements.txt
May 28  09:51  data: rubric.json and sample-transcripts.json
May 28  10:38  backend: health check + sample loader endpoints
May 28  11:22  backend: Ollama integration, 3-layer JSON extraction, repair()
May 28  14:05  backend: prompt v1 — basic structure, tested against samples
May 28  16:30  backend: prompt v2 — 6/7 boundary, survivability, bias heuristics
May 28  17:48  frontend: skeleton — input panel, sample loader, health indicator
May 29  09:55  frontend: SSE client, progress bar, phase state machine
May 29  11:10  frontend: evidence cards, layer ratio bar, score hidden card
May 29  13:40  frontend: gap analysis, follow-up questions, bias flags sections
May 29  15:25  frontend: deliberation panel and reveal button
May 29  17:05  frontend: score reveal, divergence warning, KPI mapping
May 30  09:20  frontend: finalize section, final report, copy to clipboard
May 30  10:15  frontend: transcript highlights + bidirectional evidence linking
May 30  11:30  backend: serve frontend as static — single port, no dev server
May 30  13:00  docs: README, start.sh
May 30  14:20  ux: rewrite deliberation questions in intern language
May 30  15:10  docs: DECISIONS.md — cuts, reversals, and what we overrode
```

Commits 5 and 6 show the prompt iteration that matters: v1 tested against all 3 sample transcripts gave lazy scores (Anil: 9, Meena: 4, Karthik: 8). v2 fixed all three by encoding the 6/7 boundary, survivability test, and bias heuristics explicitly. Commit 17 shows the UX revision after realizing the deliberation questions were written for someone who had read the rubric documentation — not for a psychology intern.
