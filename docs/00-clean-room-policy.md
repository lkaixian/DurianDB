# Clean-room policy

Checked 2026-10-19.

## Why

The Mango `ma-core-public` repository is public to read, but its `LICENSE` is the Radix IoT End User License Agreement. As summarized from that file, it is proprietary, not copyleft, and prohibits copying, reverse engineering, modifying and redistributing the software. Verify the current text yourself before relying on this summary: https://github.com/infiniteautomation/ma-core-public/blob/main/LICENSE

## Rules

1. Sources allowed: Radix IoT public documentation (https://docs.radixiot.com), the public Swagger/API reference, release notes, vendor-published material, and observed behavior of a legitimately licensed installation if the user has one.
2. Sources not used: the Mango source tree, decompiled or disassembled binaries, proprietary module jars.
3. No Mango code, schemas, identifiers copied verbatim, or text copied from the docs. Notes are paraphrased and cite their source URL.
4. DurianDB's design is derived from the concepts, then built independently. Where the docs are silent, we design our own answer and record it as our decision, not as Mango's behavior.
5. Every research note lists its sources and what it could not confirm.

## Consequence for "reverse engineering"

Here that means studying documented behavior and interfaces and inferring the architecture from them. It does not mean reading or decompiling the implementation.
