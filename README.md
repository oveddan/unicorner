# Unicorner

**AI reads your visual scene and gives you a controller to play it.**

A hackathon prototype from the [Music Hackspace Lisbon hackathon](https://musichackspace.org/events/hackathon-lisbon-spring-2026) (16–17 May 2026, Algoriddim track).

A DJ walks into a club with a screen and has nothing to put on it. That's the problem. Most "AI generates a UI" work treats the UI as the output; Unicorner treats the UI as the side effect. The interesting work is **reading the scene** — walking the parameter catalog of a TouchDesigner project, inferring what each control does musically and visually, and surfacing a small, opinionated, playable subset for the iPad. Optional routings wire [djay Pro](https://www.algoriddim.com/) signals (BPM-locked LFOs, beat envelopes, bass-driven modulation) directly into the scene.

## What it does

- **Reads any TouchDesigner scene** — walks the parameter tree of marked Layer B modules + curated built-ins, builds a catalog of every modulatable param with semantic hints.
- **Chats with the DJ** — multi-turn per scene. "Make me a beat-reactive intensity control." Ambiguous prompts come back as 2–3 substantively different alternatives or a clarifying question.
- **Generates a playable iPad surface** — knobs, toggles, macros, with curves shaped for live play. Knob moves write straight back to the TD param over WebSocket.
- **Wires music into the scene** — optional routings drive scene params from djay signals: direct mappings, BPM-locked LFOs, beat envelopes, bar resets.
- **Learns your physical controller** — MIDI learn modal binds hardware knobs to rendered widgets.

## How it works

The DJ loads a TouchDesigner scene. The AI reads what's in it. A controller built for that scene appears on the iPad. The DJ plays it.

```mermaid
flowchart LR
    A["🎨 load any<br/>TouchDesigner scene"]
    B["🤖 AI reads<br/>what's in it"]
    C["📱 controller appears<br/>on your iPad"]
    D["🎧 play visuals<br/>like an instrument"]

    A --> B --> C --> D
    D -.->|swap the scene| A
```

Under the hood, three layers. Music flows down the left spine (A → B → visuals). The unique part is the loop on the right: Layer B publishes a machine-readable parameter catalog, an LLM turns it into a controller spec, and the rendered UI writes back into the same visual modules over WebSocket. Optional *routings* — emitted by the AI alongside the controls — wire djay signals straight into scene params with the right modulation shape (direct, BPM-locked LFO, beat envelope, bar reset). Swap the scene → new catalog → AI regenerates the controller.

```mermaid
flowchart TD
    subgraph A["Layer A — music signals"]
        djay["djay Pro<br/>BPM · beat · stems · EQ · FX"]
    end

    subgraph B["Layer B — visual scene"]
        modules["parameter-tagged<br/>visual modules"]
        catalog[("parameter catalog<br/>JSON")]
        modules -->|exposes| catalog
    end

    subgraph C["Layer C — unicorner_controller.tox"]
        gen["in-TD generator<br/>Python · Anthropic API"]
        ws["Web Server DAT<br/>:9980"]
        gen -->|ControllerSpec| ws
    end

    ipad["📱 iPad — React controller<br/>knobs · pads · chat · MIDI learn"]

    djay -->|drives| modules
    djay -.->|"optional routings:<br/>direct · LFO · beat envelope"| modules
    catalog -->|prompt| gen
    ws <-->|WebSocket| ipad
    ipad -->|param writes| modules
```

No Node bridge. No MCP at runtime. No `pip install`. The generator calls Anthropic directly from TouchDesigner's bundled Python; everything is bundled in a single `.tox`.

## Try it

1. Open TouchDesigner. Drag `td/unicorner_controller.tox` onto your project.
2. Set the `Apikey` parameter on the COMP (or `ANTHROPIC_API_KEY` env var, or `td/.unicorner_config.json`).
3. Open `http://localhost:9980` on an iPad on the same network.
4. Open the ⚙ Designer drawer → "🔍 Scan scene" → chat a prompt → controls appear.

Releases ship a zipped `.tox` + built controller dist on every push to `main`. See [`.github/workflows/release.yml`](.github/workflows/release.yml).

For local UI work: `cd controller && npm run dev`.

## Status

Hackathon prototype, not actively maintained.

- **Full writeup** — what shipped, the pivots, the demo story, Richie + judge feedback, where this could go next: [HACKATHON.md](HACKATHON.md)
- **Original plan** (pre-build, kept as historical artifact): [PLAN-original.md](PLAN-original.md)
- **TD-side dev notes + gotchas**: [CLAUDE.md](CLAUDE.md)

## Team

| Person | Workstream |
|---|---|
| Dan | Layer C: AI scene reader + controller generator + iPad renderer |
| Calin | Layer A integration + Layer B visual modules |
| Bernardo | Visual identity (with Calin) + controller UX (with Dan) |
| Guille | Audio signal extraction (Layer A augmentation) |
| Eugene | QA, integration testing, music selection |
