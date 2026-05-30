# Product Decisions

This document records specific choices made during the build — what was considered, what was cut, what was reversed, and why. This is not a features list. It's a thinking log.

---

## Things we decided NOT to build

### ✗ A confidence slider on each evidence item

Early design had the intern rate each evidence quote: "How confident are you this quote supports the interpretation?" (1–5 stars).

**Why we cut it:** It added friction at the wrong moment. The intern has just read a 10-minute transcript, they're doing a lot of cognitive work already. Asking them to rate 6–8 individual quotes before they've even formed a view of the whole picture is backwards. The accept/reject binary is enough — it forces a clear judgment without overloading. We can always add granularity later if interns ask for it.

---

### ✗ Multiple LLM calls (one per section)

The first architecture plan used 4 separate Ollama calls: one for evidence extraction, one for scoring, one for gap analysis, one for follow-up questions.

**Why we cut it:** On a local CPU-bound model like llama3.2, each call takes 45–90 seconds. 4 calls = up to 6 minutes before the intern sees anything. The "10 minute target" in the brief becomes impossible. More importantly, the sections are interdependent — the follow-up questions should reference the specific gaps, and the scoring justification should cite the actual extracted evidence. Splitting into 4 calls means each section is blind to what the others found.

One structured prompt with a rigid JSON schema gives the model a single clear target. Temperature 0.1 keeps it deterministic. This was the right tradeoff.

---

### ✗ Showing bias flags after the score reveal

First version showed supervisor bias flags at the bottom of the page — below the score card, almost as a footnote.

**Why we reversed it:** Bias flags shown after the score are decoration. The intern has already anchored to the number; the flag doesn't change how they read the evidence. Bias flags shown *before* the score reveal change how the intern reads the evidence. We moved them to Phase 1, visible while the score is still hidden. The sequencing is the feature.

---

### ✗ Technical labels on the deliberation questions

Early deliberation panel had visible tags on each question: `6 vs 7 boundary`, `systems vs execution`, `score correction`, `before AI reveal`.

**Why we cut them:** The intern is a psychology graduate, not a software developer. Labels like "6 vs 7 boundary" mean nothing to them — they haven't memorised the rubric. Worse, the labels signal that these are technical checkboxes to complete, not genuine reflective questions. We stripped all the tags and rewrote the questions in plain language. The underlying logic is the same; what changed is who it feels like it was built for.

---

## One thing the AI was wrong about, and we overrode it

During prompt testing, early versions of the prompt instructed the model to provide a confidence percentage (e.g., "72% confident this is a Score 6"). 

The model would generate numbers like "I am 68% confident" with no real basis — it was producing false precision that sounded authoritative. An intern reading "68% confident" would treat it differently from "medium confidence," even though they mean the same thing. We replaced numerical confidence with `low / medium / high` labels plus a plain-English explanation of what's missing. Less precise, more honest.

---

## The one decision we'd defend most strongly

**Hiding the score until after deliberation** is not a UI trick. It's the core product philosophy.

The assignment brief says "the tool does NOT replace the intern's judgment." Every other design choice we could have made — showing the score immediately, making deliberation optional, putting the score in the header — would have made that sentence untrue in practice, even if it stayed true on paper.

If the score is visible while the intern reads the evidence, the intern is not forming a judgment. They're looking for reasons to agree or disagree with a number they've already seen. That's a fundamentally different cognitive task. The hidden score forces the harder, more valuable task first.

This is the decision the recruiter will either get immediately or won't. We're betting they get it.
