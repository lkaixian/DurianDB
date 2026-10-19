# DurianDB console: design direction

Source: the owner's answers in the 2026-10-20 session. The agent only formats them.

**Identity.** An engineering and operations console for plant engineers who configure sources and points all day and watch alarms. It should feel like an admin console they have used before: cool and plain, dense tables, a dark top bar, labelled forms. It studies Mango's public look and the lab instance only; no assets or markup are copied.

**Dial:** ENERGY 1 / RHYTHM 1 / MOTION 1. Uniform screens are deliberate: an operator learns one layout and finds everything where they expect it. Nothing animates.

**Palette (flat, no gradients).**
- Surface: cool grey `#f2f4f7`, white fields `#ffffff`. Dark theme `#14181d` with fields `#1b2128`.
- Ink: slate `#1a2430`, muted `#4b5766`. Dark theme ink `#e6eaef`, muted `#a0abb8`.
- Top bar: `#1f2a37` with `#eef2f6` text (dark theme `#0e1217`).
- One accent: rust `#9a3b00` (dark theme `#ff9a5c`). It marks only what needs a decision: unacknowledged alarms, limit lines, errors and form errors.
- Buttons and borders use ink, not a second colour.

**Type.** System sans, tabular numerals for live values, monospace only for JSON and expressions where alignment matters.

**Forms.** Labelled fields grouped by purpose, a hint under each field, the server's message shown under the field it names, and the raw JSON kept behind an "Advanced" disclosure for anything the form does not cover.

**Never.** Gradients, glass, glow, decorative icons or emoji, invented numbers, colour as the only signal.
