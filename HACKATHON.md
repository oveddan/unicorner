# Unicorner — hackathon writeup

> "If you DJ at a club with a screen, you have nothing to put on it. This makes it easy."
> — Richie Hawtin, after the demo

Built at the [Music Hackspace Lisbon hackathon](https://musichackspace.org/events/hackathon-lisbon-spring-2026), 16–17 May 2026, Algoriddim track.

The one-line pitch: **AI reads an arbitrary TouchDesigner scene and generates a playable control surface from whatever's in it** — knobs, toggles, macros, plus optional modulation routings driven by [djay Pro](https://www.algoriddim.com/) signals (bass → emit, BPM-locked LFOs, beat envelopes).

## What this actually became

The original plan was narrower: a clean control screen for a hand-built scene. Layer A = djay signals, Layer B = parameter-tagged visual modules, Layer C = an AI-generated UI for those modules.

Mid-build, the interesting part flipped. Layer C wasn't the AI generating a UI — UI generation is mechanical once you have the spec. The interesting part was **the AI reading the scene**: walking the parameter catalog of a stranger's TouchDesigner project, inferring what each control does musically and visually from its name + type + range, and emitting an opinionated subset shaped for live play. That's the axis worth pursuing.

So the project quietly stopped being "a control screen" and started being "an AI that reads creative tools and surfaces what's expressive about them." We didn't reframe the demo around that, which cost us — see below.

## Architecture, as shipped

```
djay Pro
  │  (Algoriddim ↔ TD integration)
  ▼
TouchDesigner scene
  ├─ Layer B modules: parameter-tagged custom params + curated built-ins
  └─ unicorner_controller (drop-in .tox)
       ├─ Generator (in-TD Python, stdlib urllib)
       │    walks catalog → Anthropic API → ControllerSpec JSON
       └─ Web Server DAT (port 9980)
            │ WebSocket
            ▼
       iPad — React controller
            ├─ renders spec as knobs / toggles / macros
            ├─ Designer drawer: scene scan, chat, alternatives, depth control
            └─ MIDI learn: bind physical hardware to widgets
```

Three layers (A: djay signals; B: parameter-tagged TD modules; C: AI-generated controller surface). The whole runtime is a single drop-in `.tox`. Drag it onto any TD project, paste an Anthropic API key into a COMP param (or env var, or `td/.unicorner_config.json` — first non-empty wins), open the iPad. No Node bridge, no MCP at runtime, no `pip install`. The generator calls Anthropic directly from TouchDesigner's bundled Python via stdlib `urllib`.

The iPad's Designer drawer is the live tuning surface. Scan the scene → get a human-readable summary the AI keeps in context for follow-ups. Chat a prompt → either get a spec or get 2–3 substantively different *alternatives* (different param sets, different curves, different macros) presented as chips. Ambiguous prompts come back as a clarifying question with answer chips. The whole thing is multi-turn per scene, with history persisted in localStorage.

## Pivots from the original plan

- **OSC → WebSocket DAT.** Eliminated the bridge process entirely. TD's WebSocket DAT is both server and parser; no separate Node process to manage.
- **Node-side generator → in-TD Python.** The original plan had the generator outside TD as a Node CLI. Moving it inside (as a Text DAT bundled into the `.tox`) made the artifact a true drop-in: one file, one API key, done.
- **Hand-described catalogs → auto-extraction.** Layer B modules export parameter metadata via tags; the catalog is walked at generation time. The DJ doesn't describe their scene; the AI reads it.
- **Static spec → multi-turn chat.** Planning-mode alternatives, clarifying questions, scene scanning, per-scene history. The DJ refines the surface conversationally instead of accepting a one-shot output.
- **Routings as first-class output.** Beyond knobs, the AI emits *routings* — direct mappings (bass → emit), LFO syncs (BPM-locked oscillations), beat envelopes, bar resets. Music modulates the scene directly; the human controls intensity and shape.

## The demo

It was rough. Both segments broke in characteristic ways, and that's the honest story.

**Demo 1 — Dan's AI-creativity angle.** Simple scene: a torus with a texture. Prompted the AI to read the scene — worked, got a clean summary. Prompted it to generate beat-reactive controls — **errored live on stage**. Recovered by switching to a pre-prepared version that already had controls connected, and showed it from there.

**Demo 2 — Calin's scene, on Windows.** We'd tried in advance to point the AI at his scene and let it generate controls automatically. The scene was big enough that we blew past Anthropic's per-request token limits with the full catalog — couldn't auto-generate end-to-end. We manually wired up some knobs as a fallback. Then on demo day, the Windows machine wouldn't connect to the venue projector. **Calin's segment never made it to the main screen.** He demoed it 1:1 to judges later — sliders + physical controller modulating the scene, working — but the room didn't see it.

That post-event token-limit experience is what drove [the catalog-depth selector](https://github.com/oveddan/unicorner/commit/fdefd3d) (full / curated / minimal) — a UI slider letting the DJ trade granularity for headroom. It's a pragmatic constraint we discovered the hard way, not one we'd designed for.

## Judge + Richie feedback

**Judges:** the two segments felt like two different projects.

That's fair. They kind of were. The first demo was about *AI creativity* — read a scene, dream up controls for it. The second was about *playing a finished scene with a real controller* — sliders, MIDI, beat-locked modulation. Both were Unicorner, but we didn't frame them together. The pivot from "control screen for a scene" to "AI that reads any scene" hadn't propagated to the demo script.

**Richie Hawtin:**
1. Zeroed in on the use case: a DJ walks into a venue with a screen and has nothing prepared to put on it. Unicorner makes that trivial — drop the `.tox` onto any TouchDesigner project on the house computer, and the DJ has a playable surface in seconds.
2. Was specifically interested in the AI side — the part where it reads the scene and generates the surface. Not just the rendering layer.

The Richie framing is the pitch. The judge feedback is the lesson — next time, one demo, one story, even if the underlying system does two things.

## What we learned the hard way

- **Token rate limits are the actual constraint on real scenes.** Calin's scene had enough parameters that the full catalog wouldn't fit in a single Anthropic request. This isn't theoretical for non-trivial projects. The catalog-depth selector (full / curated / minimal) is the mitigation.
- **`.tox` portability is fiddly.** The first releases had absolute path bugs (PRs [#19](https://github.com/oveddan/unicorner/pull/19), [#21](https://github.com/oveddan/unicorner/pull/21)), `me.path` captured on the wrong thread (`de2e408`), and Windows-specific Distpath resolution (`fbe7ce1`). Relative paths from inside a COMP need care.
- **TD custom params silently clamp to [0, 1]** if `clampMin`/`clampMax` are on and you've only set the soft range. Writes appear to succeed but the param doesn't move. Set both ranges, or leave clamping off.
- **Docs drift under time pressure.** Code review caught the README still saying "OSC" after we'd shipped on WebSocket ([PR #12](https://github.com/oveddan/unicorner/pull/12)). Worth a CI step to grep for stale architecture words.
- **The TouchDesigner MCP integration was the right scaffolding choice but the wrong runtime choice.** Building POCs and the .tox structure through MCP from Claude Code was massively faster than clicking around in TD. But shipping MCP as the runtime would have meant every user installing the bridge — wrong tradeoff. So MCP stayed in the build pipeline; the runtime became self-contained.

Full gotchas list in [CLAUDE.md](CLAUDE.md) under "TD gotchas learned the hard way."

## What didn't ship

- **v2 — auto-mapping for bring-your-own physical MIDI controllers.** Partial: MIDI input + a learn-mode modal for manually binding physical controls to widgets is in. The "AI infers a mapping when you plug in a new controller" piece isn't.
- **v3 — autopilot.** AI plays the controller for you, driven by the music. Not started.
- **v4 — background MCP agent reconfiguring TouchDesigner itself.** Not started.

## Where this goes next

Stay deep on TouchDesigner + djay Pro. Don't generalize horizontally to other tools — generalize *vertically*, by getting much better at the thing this already does.

Concrete directions:

- **Better scene reading.** This is the limiting factor on real scenes. Calin's scene blew past token limits because the AI saw every parameter equally; it should learn to read structure — what's a control surface vs. what's plumbing, which params are coupled, what a module's "intent" is from how its internals are wired. The catalog walker is naive; the read of the scene is the unlock.
- **Fluency in the modulation grammar.** djay Pro signals don't reach scene params raw — they pass through the routing primitives the system already supports: direct mappings, BPM-locked LFOs, beat envelopes, bar-reset phase, triggered-speed accumulators. The AI needs to know which envelope shape fits which musical intent (kick → beat envelope; build → slow LFO; vocal → continuous direct; drop → bar-reset LFO at high multiplier) and chain them to scene params correctly. Right now it picks competently from a handful of in-prompt examples; it should pick like a producer who knows the toolkit cold.
- **Teach it the existing patterns.** Beyond the modulation grammar itself, DJ↔visual wiring has idioms — bass-to-emit, beat-to-pulse, BPM-locked LFO sweeps, vocal-driven masks. We should give the model a library of known-good wirings as exemplars instead of having it rediscover them from first principles each time. Faster, more reliable, more musical.
- **Discover new mappings.** djay Pro exposes a lot more than we used — per-deck EQ, faders, cues, FX state, key, transition signals. The AI can experiment with mappings we wouldn't think of (key changes driving palette shifts, fader crossfade driving spatial blend between scenes). The interesting research is which djay signals actually produce expressive visual changes.
- **Beyond djay Pro: Beat Link Trigger.** djay is one DJ application; standard CDJ booths are everywhere. [Beat Link Trigger](https://github.com/Deep-Symmetry/beat-link-trigger) is an open-standard tool that talks to Pioneer CDJs over the DJ Link protocol and exposes beat grid, BPM, position, and cues. Pointing Unicorner at Beat Link Trigger instead of (or in addition to) djay Pro opens this up to any club with a CDJ rig — which is what Richie's "DJ in a club with a screen" use case actually means in practice.
- **The autopilot axis.** Beat-aware automation of the controls themselves — the AI plays alongside the DJ rather than just laying out instruments for them.

The hackathon proved the loop works. Real scenes, real signals, real iPad, real DJ. What's left is depth — and reaching past the djay-Pro-shaped hole into the wider CDJ world.

## Team

| Person | Workstream |
|---|---|
| Dan | Layer C: AI scene reader + controller generator + iPad renderer |
| Calin | Layer A integration + Layer B visual modules |
| Bernardo | Visual identity (with Calin) + controller UX (with Dan) |
| Guille | Audio signal extraction (Layer A augmentation) |
| Eugene | QA, integration testing, music selection |

---

## Appendix — portfolio blurb

> [hero shot / GIF slot: iPad showing the generated controller, TD scene reacting]

**Unicorner — AI that reads a creative tool and gives you something to play.**

Most "AI-generated UI" work treats the UI as the output. Unicorner treats the UI as the *side effect*. The interesting work is reading the scene: walking a stranger's TouchDesigner project, inferring from parameter names + types + ranges what each control does musically and visually, and surfacing a small, opinionated, playable subset. The iPad rendering is mechanical; the model's read of the scene is the artifact.

Built over a weekend at the Music Hackspace Lisbon hackathon (Algoriddim track, May 2026) with Calin, Bernardo, Guille, and Eugene. Drop-in TouchDesigner COMP, in-process Anthropic calls from TD's bundled Python, React + WebSocket iPad controller, optional djay Pro signal routings (BPM-locked LFOs, beat envelopes, bass-driven modulation). Richie Hawtin liked the venue use case: a DJ walks into a club with a screen and now has something to play on it.

Next: get much sharper at reading TouchDesigner scenes (this is the limit on real scenes, not the UI generation), teach the model the wiring idioms DJ visual artists already use, and extend the signal source from djay Pro to Beat Link Trigger so this works in any CDJ booth.

[Code](https://github.com/oveddan/unicorner) · [Writeup](HACKATHON.md)

---

## Appendix — X post draft

**Single-image / GIF version:**

> "If you DJ at a club with a screen, you have nothing to put on it. This makes it easy." — Richie Hawtin
>
> Built Unicorner at @musichackspace Lisbon: AI reads a TouchDesigner scene and generates a playable iPad controller from it in seconds. Drop-in .tox, no install.
>
> [GIF: scan scene → chat prompt → controls appear → DJ plays]
>
> github.com/oveddan/unicorner

**Thread version (5 tweets):**

1/ DJs walk into venues with screens and have nothing to play on them. So we built Unicorner: AI that reads a TouchDesigner scene and generates a playable iPad controller from whatever's in it.

2/ The interesting part isn't the controller. UI generation is mechanical once you have a spec. The interesting part is the AI *reading the scene* — inferring from parameter names, types, and ranges what each control does musically and visually.

3/ Drop the .tox onto any TD project, paste an API key, open the iPad. Scan the scene, chat a prompt, controls appear — knobs, toggles, macros, plus optional routings that wire djay Pro signals (bass, beat, BPM) straight into the scene.

4/ Built at @musichackspace Lisbon (Algoriddim track) with Calin, Bernardo, Guille, and Eugene. Richie Hawtin: "If you DJ at a club with a screen, you have nothing to put on it. This makes it easy."

5/ Code + writeup: github.com/oveddan/unicorner. Next: sharper scene reading on real TD projects, a library of known-good DJ↔visual wiring idioms, and Beat Link Trigger support so this works in any CDJ booth — not just djay Pro.
