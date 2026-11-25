# Fortuna in practice: three use cases

**Fortuna** is the AI Ecosystem's decision backend (`http://fortuna:8009`). It answers typed questions
about a piece of text: yes/no, pick one of N labels, or a level on a scale. Each answer comes with a
calibrated probability. It generates no text. The engine today is JevK5, a 4B Qwen model with a
distilled LoRA, run as a GGUF on llama.cpp: Q8 on the Strix Halo, Q4 beside KoboldCPP on a 12 GB
RTX 4070.

Before Fortuna, the frontends got these answers by prompting the main language model (KoboldCPP,
24B on the Halo) for a label and parsing its reply. That was slow and queued behind story
generation, and a free-text answer could be malformed or cut off. Three frontends moved those
decisions to Fortuna. In each one, KoboldCPP or a plain "skip" stays as the fallback: if Fortuna is
down or unsure, things get slower, never broken.

---

## 1. Whimsy: roleplay chat and RPG campaigns

*First adopter, 2026-11-17*

**Uses:** Fortuna made three judgments about every reply or campaign turn:
- **The reply's emotion**, which drives the character's mood and relationship. Fortuna picks from
  28 labels.
- **Guard my character:** "Does this sentence decide something for the player's character?" A
  sentence is removed at p ≥ 0.3.
- **Campaign promotions:** "Does this character now matter to the story?" Fortuna suggests a
  promotion at p ≥ 0.9, alongside KoboldCPP's extraction call.

**Benefits:**
- **Campaign turns are recorded in ~21 s instead of ~34 s,** and a turn's record is no longer lost.
  The promotions had made up 46 % of KoboldCPP's extraction output (and only 21 of 145 were kept).
  They also helped push 26 of 339 calls into the token cap, which lost the whole record on 9
  turns. With the promotions moved to Fortuna, a replay of 16 turns of a real campaign lost no
  scene move or time change, and kept 99 feelings against 44.
- **The guard is twice as fast and more accurate.** It took 1.6 s against 3.3 s per turn, and it
  runs before the turn commits, so the player waits for it. On 34 hand-labelled sentences Fortuna
  got 33 right at 0.3. On the first 16, KoboldCPP got 11.
- **The emotion label:** 8/8 agreement with KoboldCPP on the first live check. Its speed only
  matched KoboldCPP's: ~0.7 s after an ai-stack fix (2.6 s before), since 28 labels take three
  passes. The gain is calibrated probabilities with nothing to parse, and one less generation
  queued on the story model.

**Lessons:** Real play logs showed the guard removing sentences that only retold the player's own
action, because the judge never saw it. Two fixes followed: the judges now see what the player
wrote, and sentences where the player can't be acting are filtered out before any judge is
asked. The guard and the emotion label still run in `compare` mode, logging both engines,
until there is enough real-play data to trust Fortuna alone.

---

## 2. Cantinero: the voice assistant

*El-Iguana/companion-assistant; 2026-11-23 onward*

**Uses:**
- **Her facial expression:** Fortuna picks the emotion of each reply for the 3D avatar. Below 0.3
  her face stays neutral.
- **Voice and music commands:** Fortuna answers four questions about a request:
  - **What is being asked?** Music, a player control, or a quick question. At ≥ 0.8 it is
    handled in code with no model turn.
  - **Which match did the user mean?** Fortuna settles ties between albums or tracks.
  - **Named or described?** For example "something upbeat".
  - **Music or audiobook?** Fortuna marked every album in the library once.
- **Memory gate:** Fortuna decides whether a user turn holds anything worth remembering long-term.
  At ≥ 0.9 "nothing", the turn is skipped.

**Benefits:**
- **Expression:** **0.23 s instead of ~7 s.**
- **Commands:**
  - "Play Back in Black": **66 s → 0.5 s**.
  - "Something upbeat": **~60 s → ~2 s**.
  - The time or the date: **~20 s → ~0.2 s**. Of 36 test lines, 35 were answered right, 1 was
    left to the model, and none were wrong.
  - Every named command now runs in about a second. That is what made hands-free voice control
    usable.
- **Memory:** measured on 48 user turns, 3 runs each:

  | | right | invented | median |
  |---|---|---|---|
  | KoboldCPP | 120/144 | **21** | 827 ms |
  | Fortuna | **138/144** | **0** | **162 ms** |

  KoboldCPP turned "Play some jazz" and "Good night Mira" into standing instructions, which then
  went into every later prompt. As a gate at 0.9, Fortuna skips 31 % of turns, none wrongly. In the
  end-to-end memory test, wrongly stored items fell from 3/15 to 1/15 (with a junk-note filter in
  code).

---

## 3. World Builder: generating 3D scenes

*2026-11-19 onward*

**Use:** Fortuna is a **second reviewer** of a generated scene. The scene is turned into short
facts ("fireplace: 1 object, against the north wall"; "door: 2 on the north wall"). Fortuna then
answers fixed, hand-written questions that apply only when the subject is in the scene:
- Is a door on the hearth wall?
- Is the room crowded with tables?
- Is a room with no windows too dark?
- How plausible is the scene, on a score?

These catch semantic layout problems that World Builder's rule catalog can't express. The verdicts
are advisory: they appear in the notes area, and count as actionable at p ≥ 0.75.

**Benefits:**
- **A review in ~0.3 s instead of 30–60 s.** A review pass through the main model would take that
  long and compete with generation for its context window. The live round trip through World
  Builder was 0.282 s.
- **It runs at the same time as generation.** On the 12 GB RTX 4070, KoboldCPP (9B Q4, 5.9 GB) and
  Fortuna (4B Q4, 3.3 GB) stayed loaded together, using 11.1 of 12.3 GB including the desktop. So
  the review never waits for the generator, and the generator never waits for the review.

**Lesson:** Fortuna can only judge what the facts say. An early "no door on the hearth wall"
verdict was a guess, because fireplaces were described as "on the floor". Once the facts said
which wall a fireplace stands against, the question became answerable. Enforcing the hearth-wall
rule in the layout solver waits until the verdicts have proved trustworthy.

---

## In short

| | Decisions moved to Fortuna | Main gain |
|---|---|---|
| **Whimsy** | emotion, guard, promotions | campaign turns 34 s → 21 s with no lost records; guard 2× faster, 33/34 right |
| **Cantinero** | expression, command routing, album picks, memory gate | commands 20–66 s → 0.2–2 s; 0 invented memories (KoboldCPP: 21) |
| **World Builder** | semantic scene review | a review in 0.3 s instead of 30–60 s, alongside generation |

**The common pattern:**
- Wherever the answer is a choice rather than prose, a small typed-decision model beats asking the
  story model. It is usually an order of magnitude faster, gives a probability instead of text to
  parse, and leaves the large model free for writing.
- It works best as a **fast first opinion with a fallback**. A threshold decides when to trust it,
  and a compare mode builds that trust from real use.
- How a question is shaped matters as much as the model: Whimsy's guard had to see the player's
  own message, and World Builder's facts had to state the wall.

*Sources:*
- *Whimsy and World Builder: each app's own development notes and measurements.*
- *Cantinero: El-Iguana/companion-assistant `CLAUDE.md`, `services/fortuna.py`,
  `eval/memory_decisions` and `eval/assistant_memory` results.*
- *Fortuna: [`CLAUDE.md`](CLAUDE.md). How the apps were changed to use it: the wiki's
  [Decision models](../docs/wiki/decision-models.md) page.*

*Measurements: 2026-11-17 to 2026-11-24.*
