# `durian-rt` — the experimental real-time core

An experiment, on the `experimental/rust-engine` branch, to answer one question: what would DurianDB's detector
layer look like if it had to hold a deadline under a hostile stream of values?

It is **not** a second implementation of the server, and it is not wired into `durian/`. The Python engine
remains the source of truth. This crate exists to be read and measured, and to inform a decision later.

## What it does

`src/lib.rs` ports the detectors from `durian/core.py`: high and low limit with deadband, rate of change,
no update, no change, change, state, range, smoothness and CUSUM. Plus:

- `Transition::Raised` / `Transition::Cleared` as values, so a caller cannot miss a clear (in Python the clear is
  a mutation of an object that `check()` returns `None` for).
- A fixed-size ring for smoothness, so memory per detector is constant for the life of the process.
- Non-finite samples counted in `Detector::rejected` and never compared, instead of `NaN > limit` being silently
  false forever.
- `Ring`, `Kind::from_config` returning every problem at once, and levels ordered as the console orders them.

No dependencies. `panic = "abort"` in release, arithmetic overflow checks on.

## What it says about the Python side

Two findings that came out of writing this, both of which the tests caught:

1. A latching detector that reports a clear must report **when it was raised**. Python's `_Latch` keeps that on
   the `Event` object it clears, which works only as long as the caller keeps the object. Here the raise time
   travels with the clear, so a caller cannot pair it with the wrong alarm.
2. A value that arrives at the **same timestamp** as the previous one is not evidence of anything. Treating it as
   "normal" cleared an active rate-of-change alarm. The Rust version returns no transition at all for a zero
   interval; the Python version divides by it and gets a rate of `inf` or `0`.

The second is a real defect in `durian/core.py` (`RateOfChange.evaluate` guards `ts <= prev[1]`, so it returns
`None` — which the latch reads as normal). It is recorded in `TECHNICAL_DEBT.md` as C15.

## Run it

```
cd rust
cargo test
cargo run -q -- 'HIGH_LIMIT limit=10 deadband=1 level=URGENT duration=5' < samples.csv
printf '0,5\n1,11\n2,12\n3,9\n' | cargo run -q -- 'HIGH_LIMIT limit=10'
```

One JSON object per transition:

```
{"event":"raised","point":"point","level":"URGENT","message":"11 > 10","ts":1}
{"event":"cleared","point":"point","raised_at":1,"cleared_at":3}
```

`--tick <seconds>` advances time with no value between samples so `NO_UPDATE` can fire; `--point <name>` names
the point in the output.

## Limits of the experiment

- Numbers only. Text points have no real-time path here.
- No persistence, no history, no network. It is the detector layer, nothing below or above it.
- The CLI parses a CSV stream; that is a harness for testing, not a supported interface.
- Windows linking needed the GNU toolchain (`rustup toolchain install stable-x86_64-pc-windows-gnu`) because
  no MSVC build tools are installed on the development machine. CI uses stable on Linux and does not care.
