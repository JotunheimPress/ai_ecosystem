# Decision models

[Wiki home](README.md) · Developer guide

**Status: an ongoing proof of concept** (since 2026-11-17). The question it tests: can an existing
LLM stack hand its *decisions* to a small, separate decision model while the large model keeps the
*writing*, without changing what users see except speed and reliability? Three frontends take part
so far, Whimsy, Cantinero and World Builder. The results per app, with measurements, are in
[`fortuna/USE_CASES.md`](../../fortuna/USE_CASES.md). This page is about **how**: what changed in
the stack and in each app's architecture, and the pattern to follow when adding a decision model to
another app.

## The idea

Many LLM calls in an app are not writing at all. They are decisions dressed up as prompts:

- "Which emotion is this reply? Answer with one word."
- "Does this sentence act for the player? Yes or no."
- "Is this album an audiobook?"

The app then parses a word out of free text. Those calls are slow because they queue behind story
generation on the same model. They are fragile, because the answer can be malformed, chatty or cut
off. And they give no confidence, only a word.

A **decision model** answers the question directly, as probabilities over a fixed set of answers:

| Type | Answers | Example |
|---|---|---|
| `noul` | yes/no, as P(yes) | "Does this sentence decide something for the player?" |
| `choice` | one of N labels, with a probability for each | "Which emotion does this reply express?" |
| `score` | a level on a scale | "How plausible is this scene?" |

Nothing is generated, so there is nothing to parse, and the probability tells the app how far to
trust the answer. In this stack the decision model is **Fortuna**: `http://fortuna:8009/v1/systemone`
(TypeSafe's request shape). Today's engine is JevK5, a 4B GGUF on llama.cpp. See
[Backends](backends.md) and [`fortuna/CLAUDE.md`](../../fortuna/CLAUDE.md).

**A rule of thumb:** if the app's code reads the model's answer with a regex, a `startswith` or a
lookup table, that call is a candidate. If a person reads the answer, it isn't.

## What adding it changed in the stack

Fortuna came in as a third backend beside text (KoboldCPP) and image and sound (ComfyUI), and
follows the same rules:

```
   before                                   after
   frontend ──► koboldcpp:5001              frontend ──► koboldcpp:5001   (writing)
     (writing AND every label,                       └─► fortuna:8009     (decisions, 0.1–0.7 s)
      one generation at a time)
                                            ai-net ── backend-router ── fortuna-local
```

- **Named after the role, not the engine.** Frontends call `fortuna`, never `jevk5`. The engine is
  a recipe (`fortuna/Containerfile`, `models.table`) and can be swapped (Kev was the alternative
  considered) without touching any app.
- **Behind the router**, like the other backends: `fortuna-local` publishes no port, and the router
  owns `fortuna:8009` on `ai-net` and `127.0.0.1:8009` on the host. A missing Fortuna is a fast
  502, never a hang.
- **No code runs when the model loads.** It is a GGUF on the official llama.cpp image, pinned by
  digest, with a stdlib-only adapter. That ruled out the PyTorch engine (see
  [`fortuna/CLAUDE.md`](../../fortuna/CLAUDE.md)).
- **It shares the GPU on purpose.** `models.table` boots Q8 (4.5 GB) on the Halo's unified memory
  and Q4 (2.7 GB) on the 12 GB 4070, next to KoboldCPP. The VRAM planner counts it. Verified on the
  4070: KoboldCPP 9B Q4 and Fortuna Q4 loaded together use 11.1 of 12.3 GB.
- **Optional for every frontend.** `stack doctor` reports it, but no app may require it (below).

## What adding it changed in the apps

Every app made the same structural change. **A decision that used to be a KoboldCPP prompt became
a typed question to Fortuna, with the old prompt kept behind it as the fallback.**

```
   before:  event ──► build prompt ──► KoboldCPP (queued) ──► parse a word ──► act

   after:   event ──► typed question ──► Fortuna ──► p ≥ threshold? ──yes──► act
                                          │   (fails or slow)     └─no───► safe default
                                          └───────────────────────────────► old KoboldCPP path
```

### The shared pieces (each app has its own copy)

1. **A small client module** in each app (Cantinero's is `services/fortuna.py`):
   - one function that sends a question and returns `(answer, probability)` or *nothing*;
   - **short timeouts.** Cantinero waits 3 s (0.5 s to connect), Whimsy 12 s (a 28-label question
     is several passes), and World Builder 3 s for the health check and 8 s for the question;
   - **a 60 s back-off after any failure,** so an install without Fortuna doesn't pay a timeout on
     every turn;
   - **never raises.** Failure is "no answer", and the caller falls back;
   - the settings `FORTUNA_URL` (empty, or `FORTUNA=off`, turns it off) and an optional bearer key.
2. **A threshold per decision,** chosen from measurement and never shared between questions. Each
   sets how much the answer has to be trusted for its own case:

   | App | Decision | Acts at |
   |---|---|---|
   | Whimsy | guard: remove a sentence | P(yes) ≥ 0.3 |
   | Whimsy | promotion | P(yes) ≥ 0.9 |
   | Cantinero | expression (else neutral) | ≥ 0.3 |
   | Cantinero | voice fast lane, album pick | ≥ 0.8 |
   | Cantinero | memory gate (skip the turn) | "nothing" ≥ 0.9 |
   | World Builder | review verdict is actionable | ≥ 0.75 |

3. **An engine switch per decision, used while trust is being built** (Whimsy, e.g. `GUARD_ENGINE`).
   It takes three values:
   - `fortuna`: Fortuna, with KoboldCPP as the fallback;
   - `kobold`: as before;
   - `compare`: runs both, acts on Fortuna, and logs each pair (labels, probabilities, timings) to
     a compare log, one per decision.

   The compare log is how a decision earns its way to `fortuna` alone. Logs hold no private text
   where the content is sensitive (labels and timings only).
4. **Tests that never reach a real backend:** `FORTUNA_URL=""` or `FORTUNA=off` in the test
   config, a mock transport for Fortuna's answers, and tests of every fallback path.

### Whimsy: decisions moved out of the turn pipeline

```
   campaign turn, before:
     narrate ─► guard (KoboldCPP 3–5 s) ─► extraction (KoboldCPP, fenced JSON incl. promotions,
                                           18–39 s, 26/339 hit the token cap) ─► commit
   after:
     narrate ─► guard (Fortuna, one yes/no per candidate, ~1.6 s) ─► extraction:
                   KoboldCPP: one-line JSON, no promotions   ┐ concurrently
                   Fortuna: "does X matter now?" per member  ┘ ─► apply ─► commit   (~21 s)
```

- **Fortuna got the decisions; KoboldCPP kept the structured record.** The scene moves stayed with
  KoboldCPP: replayed, Fortuna caught only 11 of 15, and a missed move means the scene never moves.
  Not every label is a good Fortuna question.
- **Questions were reshaped, not just moved.** A single 0–7 scale put neutral passages on the
  first rung. Two questions ("is there any at all?", then "how much?") fixed it.
- **Context the judge needs must be sent.** The guard removed sentences that retold the player's
  own action until the player's message was included in the question. Code then filtered
  out sentences where the player can't be the one acting, before any judge is asked.

### Cantinero: decisions in front of the model turn

```
   voice request, before:  text ─► model turn with tools (KoboldCPP) ─► tool ─► reply    20–66 s
   after:                  text ─► Fortuna: what is asked? (0.15 s)
                                     ├─ play / control / quick question, p ≥ 0.8 ─► done in code   0.2–2 s
                                     └─ anything else ─► one model pass, as before                 ~20 s
```

- **Fortuna as a router in front of the LLM.** Requests it is sure of never reach the model.
  Unsure ones go to her exactly as before, so the worst case is the old behaviour.
- **Fortuna as a gate in front of an LLM job** (memory). It doesn't replace the extractor; it
  removes turns it is ≥ 0.9 sure hold nothing. Measured: 0 invented memories against KoboldCPP's
  21, and its few misses scored under 0.9, so they still reached the extractor.
- **Fortuna as a one-time labeller** (the music catalog): every album was marked music or spoken
  word once, and the result is cached. The cost is paid once, not per request.
- **Fortuna as a cheap replacement** where a fallback is harmless: the avatar's expression (0.23 s
  instead of ~7 s; an unsure answer means a neutral face).

### World Builder: a second reviewer beside the generator

```
   generate scene (KoboldCPP) ─► solver + rule catalog ─► reveal scene
                                         └─► scene facts ─► Fortuna questions ─► verdicts in notes
                                             (non-blocking, after the reveal; ~0.3 s)
```

- **Text is rendered for the decision model.** A 3D scene is turned into short factual sentences,
  and a question is asked only when its subject is present (no hearth-wall question without a
  fireplace and a door).
- **Fortuna can only judge what the facts say.** A fireplace described as "on the floor" made
  "is a door on the hearth wall?" a guess. The fix was in the facts, not the model.
- **It runs at the same time as generation.** This is a requirement, not an optimisation, and it is
  why the GPU budget above matters.
- **Advisory first.** Verdicts are shown, not enforced. A rule becomes a solver constraint only
  after its verdicts have proved trustworthy.

## Adding a decision model to an app: the pattern

1. **Find the decisions.** List every LLM call whose answer is parsed, not read. Note its latency
   and how often the parse fails.
2. **Write the question as a type.** Choose `noul`, `choice` or `score`, with fixed options and a
   one-sentence description for each. Split a hard question into two easy ones. Put everything
   the judge needs into the state, and nothing it doesn't.
3. **Keep the old path as the fallback,** behind a client that never raises, has short timeouts,
   and backs off for 60 s after a failure.
4. **Measure before trusting.** Label a small set by hand (16–50 items is enough to see the
   shape), compare both engines, and pick the threshold from the data. Say whether an
   overestimate or an underestimate is the costly error, and set the threshold to avoid that one.
5. **Ship in `compare` mode** when the decision changes what the user sees, and move to `fortuna`
   once real use agrees.
6. **Prefer gates and routers over replacements.** Let Fortuna remove work it is sure of, and let
   the LLM keep the rest. This made every app faster without making it worse.

## Where the POC stands

| | Status |
|---|---|
| Whimsy | Live since 2026-11-17. Promotions on `fortuna`; the other decisions still on `compare` |
| Cantinero | Live since 2026-11-23 for the expression, voice and music lane, quick answers, catalog and memory gate |
| World Builder | Reviewer built, advisory; the live page check and enforcing the first rule are pending |

**Open questions:**
- JevK5's published accuracy is self-reported. The apps' own labelled sets are the evidence so
  far, and they are small.
- Which questions should *not* move? So far: scene moves (Whimsy) and anything generative.
- Should apps share a client library? Today each has its own small module, which keeps them
  independent but duplicates the back-off and the compare logging.
- Swapping the engine: behind `fortuna:8009` nothing in the apps changes, but each threshold
  above would need re-measuring.

Results and numbers: [`fortuna/USE_CASES.md`](../../fortuna/USE_CASES.md). Engine notes:
[`fortuna/CLAUDE.md`](../../fortuna/CLAUDE.md).
