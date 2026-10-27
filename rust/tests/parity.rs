//! Replays the Python detector vectors against the Rust state machine.
//!
//! Each `vectors/*.txt` file is produced by `tools/rust_parity_vectors.py` from the real detectors in
//! `durian/core.py`. The crate is only a faithful experiment while the two report the same state on the
//! same samples, so a mismatch here is a FINDING, not a test to relax: do not edit the vector to match
//! the crate, and do not edit the crate to match the vector. The Python engine is the reference the
//! server ships; the Rust side is the thing under study.
//!
//! Format (plain text, no dependencies):
//!   kind <NAME>
//!   config <key>=<value> ...
//!   delays <duration> <off_delay>
//!   samples
//!   C <ts> <value>      # a value arrived
//!   T <now> <last_recv> # time passed
//!   expected
//!   <NORMAL|ACTIVE|RAISED|CLEARED>   one line per sample

use std::fs;
use std::path::{Path, PathBuf};

use durian_rt::{Detector, Kind, Level, Transition};

struct Vector {
    kind: Kind,
    duration: f64,
    off_delay: f64,
    ops: Vec<(char, f64, f64)>,
    expected: Vec<String>,
}

fn kind_from(name: &str, config: &[(String, f64)]) -> Kind {
    if name == "STATE" {
        let states = config.iter().filter(|(key, _)| key == "states").map(|(_, value)| *value).collect();
        // The Python vector's state labels only shape the message text, which is not compared.
        return Kind::State { states, labels: Vec::new() };
    }
    let get = |key: &str| config.iter().find(|(k, _)| k == key).map(|(_, value)| *value);
    Kind::from_config(name, &get).unwrap_or_else(|errors| panic!("vector kind {name}: {errors:?}"))
}

fn parse(text: &str, file: &Path) -> Vector {
    let (mut kind_name, mut config) = (String::new(), Vec::new());
    let (mut duration, mut off_delay) = (0.0, 0.0);
    let (mut ops, mut expected) = (Vec::new(), Vec::new());
    let (mut in_samples, mut in_expected) = (false, false);
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut words = line.split_whitespace();
        let head = words.next().expect("a non-empty line has a head");
        match head {
            "kind" => kind_name = words.next().unwrap().to_string(),
            "config" => {
                for token in words {
                    let (key, value) = token
                        .split_once('=')
                        .unwrap_or_else(|| panic!("{}: bad config token {token}", file.display()));
                    for part in value.split(',') {
                        config.push((key.to_string(), part.parse().unwrap_or_else(|_| {
                            panic!("{}: bad config value {value}", file.display())
                        })));
                    }
                }
            }
            "delays" => {
                duration = words.next().unwrap().parse().unwrap();
                off_delay = words.next().unwrap().parse().unwrap();
            }
            "samples" => in_samples = true,
            "expected" => {
                in_samples = false;
                in_expected = true;
            }
            "C" | "T" if in_samples => {
                let ts = words.next().unwrap().parse().unwrap();
                let arg = words.next().unwrap().parse().unwrap();
                ops.push((head.chars().next().unwrap(), ts, arg));
            }
            _ if in_expected => expected.push(head.to_string()),
            _ => panic!("{}: line outside its section: {line}", file.display()),
        }
    }
    assert!(!kind_name.is_empty(), "{}: no kind header", file.display());
    assert_eq!(ops.len(), expected.len(), "{}: samples and expected tokens disagree", file.display());
    Vector { kind: kind_from(&kind_name, &config), duration, off_delay, ops, expected }
}

#[test]
fn vectors_match_python_detector_state_machine() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("vectors");
    let mut files: Vec<PathBuf> = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("cannot read {}: {e}", dir.display()))
        .map(|entry| entry.expect("directory entry").path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "txt"))
        .collect();
    files.sort();
    assert!(!files.is_empty(), "no vectors in {}", dir.display());

    let mut mismatches = Vec::new();
    for path in files {
        let text = fs::read_to_string(&path).unwrap();
        let vector = parse(&text, &path);
        let mut detector = Detector::with_delays(vector.kind, Level::Warning, vector.duration, vector.off_delay);
        for (index, (op, ts, arg)) in vector.ops.iter().copied().enumerate() {
            let transition = match op {
                'C' => detector.check(ts, arg),
                'T' => detector.tick(ts, arg),
                other => panic!("{}: unknown op {other}", path.display()),
            };
            let actual = match transition {
                Some(Transition::Raised(_)) => "RAISED",
                Some(Transition::Cleared { .. }) => "CLEARED",
                None if detector.is_active() => "ACTIVE",
                None => "NORMAL",
            };
            let expected = vector.expected[index].as_str();
            if actual != expected {
                mismatches.push(format!(
                    "{} sample {index} (t={ts}, arg={arg}): Rust {actual}, Python {expected}",
                    path.display()
                ));
            }
        }
    }
    assert!(mismatches.is_empty(), "Rust/Python detector parity failed:\n{}", mismatches.join("\n"));
}
