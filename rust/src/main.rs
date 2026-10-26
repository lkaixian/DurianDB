//! A small driver for the detector core, so it can be exercised from a shell or a test harness.
//!
//! Usage: `durian-rt 'HIGH_LIMIT limit=10 deadband=1 level=URGENT duration=5' < samples.csv`
//! where samples.csv holds `timestamp,value` per line (`#` starts a comment, timestamps are seconds).
//! It prints one JSON object per transition, which is the whole interface: the Python server would call
//! `Detector::check` directly rather than shelling out.
//!
//! `--tick <interval>` also advances time with no value between samples, so NO_UPDATE fires as it would in the
//! engine's periodic tick.

use std::io::{self, BufRead, Write};

use durian_rt::{Detector, Kind, Level, Transition};

fn level_of(s: &str) -> Option<Level> {
    Some(match s.to_ascii_uppercase().as_str() {
        "NONE" => Level::None,
        "INFORMATION" => Level::Information,
        "IMPORTANT" => Level::Important,
        "WARNING" => Level::Warning,
        "URGENT" => Level::Urgent,
        "CRITICAL" => Level::Critical,
        "LIFE_SAFETY" => Level::LifeSafety,
        _ => return None,
    })
}

fn usage() -> ! {
    eprintln!("usage: durian-rt '<KIND key=value ...>' [--tick seconds] < timestamp,value per line");
    std::process::exit(2)
}

/// Parse `KIND key=value ...`. Unknown keys are refused rather than ignored: a typo must not silently
/// become the default, which is how a limit ends up at zero in production.
fn parse_spec(spec: &str) -> (Kind, Level, f64, f64) {
    let mut words = spec.split_whitespace();
    let name = words.next().unwrap_or_else(|| usage()).to_ascii_uppercase();
    let mut settings: Vec<(String, f64)> = Vec::new();
    let mut level = Level::Warning;
    let (mut duration, mut off_delay) = (0.0, 0.0);
    for w in words {
        let Some((k, v)) = w.split_once('=') else {
            eprintln!("{w:?} is not key=value");
            usage();
        };
        match k {
            "level" => match level_of(v) {
                Some(l) => level = l,
                None => {
                    eprintln!("unknown level {v:?}");
                    usage()
                }
            },
            "duration" => duration = v.parse().unwrap_or_else(|_| usage()),
            "off_delay" => off_delay = v.parse().unwrap_or_else(|_| usage()),
            "inside" => settings.push((k.into(), if v == "true" || v == "1" { 1.0 } else { 0.0 })),
            _ => match v.parse() {
                Ok(n) => settings.push((k.into(), n)),
                Err(_) => {
                    eprintln!("{k} needs a number, not {v:?}");
                    usage()
                }
            },
        }
    }
    let get = |k: &str| settings.iter().find(|(s, _)| s == k).map(|(_, v)| *v);
    let kind = match Kind::from_config(&name, &get) {
        Ok(k) => k,
        Err(problems) => {
            for p in problems {
                eprintln!("{p}");
            }
            std::process::exit(2);
        }
    };
    (kind, level, duration, off_delay)
}

fn emit(mut out: impl Write, point: &str, t: &Transition) {
    let line = match t {
        Transition::Raised(a) => format!(
            "{{\"event\":\"raised\",\"point\":{point:?},\"level\":\"{}\",\"message\":{:?},\"ts\":{}}}",
            a.level.name(),
            a.message,
            a.ts
        ),
        Transition::Cleared { raised_at, cleared_at } => format!(
            "{{\"event\":\"cleared\",\"point\":{point:?},\"raised_at\":{raised_at},\"cleared_at\":{cleared_at}}}"
        ),
    };
    // a closed stdout (a test harness that stopped reading) is not worth a panic
    let _ = writeln!(out, "{line}");
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let spec = args.first().cloned().unwrap_or_else(|| usage());
    let mut tick: Option<f64> = None;
    let mut point = "point".to_string();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--tick" if i + 1 < args.len() => {
                tick = args[i + 1].parse().ok();
                if tick.is_none() {
                    eprintln!("--tick needs a number of seconds");
                    usage();
                }
                i += 2;
            }
            "--point" if i + 1 < args.len() => {
                point = args[i + 1].clone();
                i += 2;
            }
            other => {
                eprintln!("unexpected argument {other:?}");
                usage();
            }
        }
    }
    let (kind, level, duration, off_delay) = parse_spec(&spec);
    let mut det = Detector::with_delays(kind, level, duration, off_delay);
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let mut last_recv: Option<f64> = None;
    let mut prev_ts: Option<f64> = None;

    for (n, line) in io::stdin().lock().lines().enumerate() {
        let Ok(line) = line else { break };
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((ts, value)) = line.split_once(',') else {
            eprintln!("line {}: expected timestamp,value", n + 1);
            continue;
        };
        let (Ok(ts), Ok(value)) = (ts.trim().parse::<f64>(), value.trim().parse::<f64>()) else {
            eprintln!("line {}: timestamp and value must be numbers", n + 1);
            continue;
        };
        // advance time to this sample first, so a gap is seen before the value it holds
        if let (Some(every), Some(prev)) = (tick, prev_ts) {
            let mut t = prev + every;
            while t < ts {
                if let Some(tr) = det.tick(t, last_recv.unwrap_or(t)) {
                    emit(&mut out, &point, &tr);
                }
                t += every;
            }
        }
        let before = det.rejected;
        if let Some(tr) = det.check(ts, value) {
            emit(&mut out, &point, &tr);
        }
        if det.rejected > before {
            // one line per bad sample: a quiet detector must not hide a NaN stream
            eprintln!("line {}: value is not a finite number", n + 1);
            continue;  // a rejected sample is not a value: it must not refresh last_recv
        }
        last_recv = Some(ts);
        prev_ts = Some(ts);
    }
    if det.rejected > 0 {
        eprintln!("{} sample(s) rejected as non-finite", det.rejected);
    }
}
