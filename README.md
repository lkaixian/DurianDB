# DurianDB

An educational reference for how a SCADA / IoT data-acquisition platform fits together, studied from the public documentation of Mango (Radix IoT). DurianDB is an independent, original work. It does not contain Mango code.

- `docs/00-clean-room-policy.md`: how research is done and what is off limits.
- `docs/research/`: dated research notes, each with its sources.
- `durian/`: a small original implementation of the concepts, for learning.
- `graphify-out/`: knowledge graph of this repo (research and code), for future maintenance.

Status: early. See `docs/research/99-open-questions.md` for what is not yet understood.

## Run

`python -m durian.server` then open http://127.0.0.1:8765. Python 3 standard library only. Data is simulated; see `durian/server.py`.

