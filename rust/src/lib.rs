//! Experimental real-time detector core for DurianDB.
//!
//! This is a deliberately small, dependency-free port of the alarm detectors in `durian/core.py`, written to
//! study what the Python engine cannot give: a hot path with no allocation, no unbounded growth, and no
//! unreachable-arithmetic panics. It is not a second implementation of the server. The Python engine remains the
//! source of truth; this side answers one question: how would the detector layer look if it had to hold a
//! guaranteed deadline under a stream of hostile values?
//!
//! Differences from the Python version, all on purpose:
//!  - Transitions are returned, not mutated onto an event the caller already has. Python's `_Latch.check`
//!    clears an alarm by writing `cleared_ts` onto the old object and returning `None`, which a caller can
//!    miss; here a clear is a value.
//!  - Values are `f64` only. Text points have no real-time path (see README: "text on Modbus and calc" is
//!    excluded there too). Detectors that need the point's state labels take them at construction.
//!  - Every sample count is bounded at construction, so memory per detector is fixed for its whole life.
//!  - Non-finite input is rejected and reported once, instead of flowing into comparisons that are always false.

use std::fmt;

/// Alarm levels, ordered the way the console orders them. Higher is more severe.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    None,
    Information,
    Important,
    Warning,
    Urgent,
    Critical,
    LifeSafety,
}

impl Level {
    pub fn name(self) -> &'static str {
        match self {
            Level::None => "NONE",
            Level::Information => "INFORMATION",
            Level::Important => "IMPORTANT",
            Level::Warning => "WARNING",
            Level::Urgent => "URGENT",
            Level::Critical => "CRITICAL",
            Level::LifeSafety => "LIFE_SAFETY",
        }
    }
}

impl fmt::Display for Level {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// How far a value can be trusted, mirroring `core.Quality`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Quality {
    Good,
    Uncertain,
    Bad,
}

/// What a detector reports. A raise and a clear are separate, so a caller cannot forget one of them.
#[derive(Clone, Debug, PartialEq)]
pub enum Transition {
    Raised(Alarm),
    /// The alarm that was raised at `raised_at` is back to normal.
    Cleared { raised_at: f64, cleared_at: f64 },
}

#[derive(Clone, Debug, PartialEq)]
pub struct Alarm {
    pub level: Level,
    pub message: String,
    pub ts: f64,
}

/// A fixed-capacity window. `push` on a full ring overwrites the oldest sample: smoothness cannot grow with
/// uptime, and a stalled detector cannot exhaust memory.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Ring {
    buf: Vec<f64>,
    next: usize,
    filled: bool,
}

impl Ring {
    pub fn new(capacity: usize) -> Ring {
        assert!(capacity > 0, "a window needs room for at least one sample");
        Ring { buf: vec![0.0; capacity], next: 0, filled: false }
    }

    pub fn push(&mut self, v: f64) {
        self.buf[self.next] = v;
        self.next = (self.next + 1) % self.buf.len();
        if self.next == 0 {
            self.filled = true;
        }
    }

    pub fn len(&self) -> usize {
        if self.filled {
            self.buf.len()
        } else {
            self.next
        }
    }

    pub fn is_full(&self) -> bool {
        self.filled
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn iter(&self) -> impl Iterator<Item = f64> + '_ {
        let n = self.len();
        let cap = self.buf.len();
        let start = if self.filled { self.next } else { 0 };
        (0..n).map(move |i| self.buf[(start + i) % cap])
    }

    /// Population standard deviation, as the Python smoothness detector computes it.
    pub fn stdev(&self) -> f64 {
        let n = self.len();
        if n == 0 {
            return 0.0;
        }
        let mean = self.iter().sum::<f64>() / n as f64;
        (self.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>() / n as f64).sqrt()
    }
}

/// The condition a detector watches. Built once; `check` never allocates.
#[derive(Clone, Debug, PartialEq)]
pub enum Kind {
    HighLimit { limit: f64, deadband: f64 },
    LowLimit { limit: f64, deadband: f64 },
    RateOfChange { per_second: f64 },
    NoUpdate { seconds: f64 },
    NoChange { seconds: f64, tolerance: f64 },
    Change { tolerance: f64 },
    State { states: Vec<f64>, labels: Vec<(f64, String)> },
    Range { low: f64, high: f64, inside: bool },
    Smoothness { limit: f64, samples: usize },
    Cusum { target: f64, slack: f64, threshold: f64 },
}

impl Kind {
    /// Read the kind out of a `kind` name and a small settings map, the way the console's form builds one.
    /// Returns every problem at once, like the Python validators do.
    pub fn from_config(name: &str, get: &dyn Fn(&str) -> Option<f64>) -> Result<Kind, Vec<String>> {
        let need = |k: &str| get(k).ok_or_else(|| vec![format!("{name} needs {k}")]);
        let opt = |k: &str| get(k).unwrap_or(0.0);
        Ok(match name {
            "HIGH_LIMIT" => Kind::HighLimit { limit: need("limit")?, deadband: opt("deadband") },
            "LOW_LIMIT" => Kind::LowLimit { limit: need("limit")?, deadband: opt("deadband") },
            "RATE_OF_CHANGE" => Kind::RateOfChange { per_second: need("per_second")? },
            "NO_UPDATE" => Kind::NoUpdate { seconds: need("seconds")? },
            "NO_CHANGE" => Kind::NoChange { seconds: need("seconds")?, tolerance: opt("tolerance") },
            "CHANGE" => Kind::Change { tolerance: opt("tolerance") },
            "RANGE" => Kind::Range { low: need("low")?, high: need("high")?, inside: opt("inside") != 0.0 },
            "SMOOTHNESS" => {
                let n = need("samples")? as usize;
                if n == 0 || n > MAX_SAMPLES {
                    return Err(vec![format!("samples must be 1 to {MAX_SAMPLES}")]);
                }
                Kind::Smoothness { limit: need("limit")?, samples: n }
            }
            "CUSUM" => Kind::Cusum { target: need("target")?, slack: opt("slack"), threshold: need("threshold")? },
            "STATE" => return Err(vec!["STATE needs its list of states; use Kind::State directly".into()]),
            other => return Err(vec![format!("unknown detector {other}")]),
        })
    }

}

/// Format a number the way Python's `:g` does for the values this code produces: no trailing zeros, no
/// `.0` on a whole number. The message text is part of the alarm an operator reads, so it stays stable.
pub fn num(v: f64) -> String {
    if !v.is_finite() {
        return format!("{v}");
    }
    if v == 0.0 {
        return "0".into();
    }
    let mag = v.abs();
    if !(1e-4..1e6).contains(&mag) {
        let s = format!("{v:e}");
        // Python writes 1.5e-07; Rust writes 1.5e-7
        return match s.split_once('e') {
            Some((m, e)) if !e.starts_with('-') && e.len() < 2 => format!("{m}e+0{e}"),
            Some((m, e)) if e.starts_with('-') && e.len() < 3 => format!("{m}e-0{}", &e[1..]),
            _ => s,
        };
    }
    let mut s = format!("{v:.6}");
    while s.contains('.') && s.ends_with('0') {
        s.pop();
    }
    if s.ends_with('.') {
        s.pop();
    }
    s
}

/// Ceiling on a smoothness window, so a configuration mistake cannot ask for unbounded memory.
pub const MAX_SAMPLES: usize = 4096;

/// One detector. The per-kind scratch lives together because a detector is only ever one kind: an enum here
/// beats a trait object, which would put an allocation and a vtable on the hot path for no benefit.
#[derive(Clone, Debug)]
pub struct Detector {
    pub kind: Kind,
    pub level: Level,
    /// On-delay: the condition must hold this long before the alarm is raised.
    pub duration: f64,
    /// Off-delay: the condition must be absent this long before the alarm clears.
    pub off_delay: f64,
    since: Option<f64>,
    ok_since: Option<f64>,
    raised_at: Option<f64>,
    prev: Option<(f64, f64)>,
    window: Option<Ring>,
    cusum: (f64, f64),
    /// Last value and the time it last moved, for NO_CHANGE.
    still: Option<(f64, f64)>,
    /// How many samples were refused as non-finite. A caller reads this to warn once, not per sample.
    pub rejected: u64,
}

impl Detector {
    pub fn new(kind: Kind, level: Level) -> Detector {
        Self::with_delays(kind, level, 0.0, 0.0)
    }

    pub fn with_delays(kind: Kind, level: Level, duration: f64, off_delay: f64) -> Detector {
        let window = match &kind {
            Kind::Smoothness { samples, .. } => Some(Ring::new(*samples)),
            _ => None,
        };
        Detector {
            kind,
            level,
            duration: duration.max(0.0),
            off_delay: off_delay.max(0.0),
            since: None,
            ok_since: None,
            raised_at: None,
            prev: None,
            window,
            cusum: (0.0, 0.0),
            still: None,
            rejected: 0,
        }
    }

    pub fn is_active(&self) -> bool {
        self.raised_at.is_some()
    }

    /// Forget any alarm in progress, without reporting a clear: the point went out of service.
    pub fn reset(&mut self) {
        self.since = None;
        self.ok_since = None;
        self.raised_at = None;
        self.prev = None;
        if let Some(w) = self.window.as_mut() {
            *w = Ring::new(w.buf.len());
        }
        self.cusum = (0.0, 0.0);
        self.still = None;
    }

    /// A value arrived. Non-finite values are counted and ignored: they never reach a comparison.
    pub fn check(&mut self, ts: f64, value: f64) -> Option<Transition> {
        if !value.is_finite() || !ts.is_finite() {
            self.rejected += 1;
            return None;
        }
        self.check_finite(ts, value)
    }

    fn check_finite(&mut self, ts: f64, value: f64) -> Option<Transition> {
        match self.kind.clone() {
            Kind::NoUpdate { .. } => {
                // its condition is about time passing, not about the value
                match self.raised_at.take() {
                    Some(raised_at) => Some(Transition::Cleared { raised_at, cleared_at: ts }),
                    None => None,
                }
            }
            Kind::NoChange { seconds: _, tolerance } => {
                if self.still.map(|(v, _)| (v - value).abs() > tolerance).unwrap_or(true) {
                    self.still = Some((value, ts));
                    if let Some(raised_at) = self.raised_at.take() {
                        return Some(Transition::Cleared { raised_at, cleared_at: ts });
                    }
                }
                None
            }
            Kind::Change { tolerance } => {
                let prev = self.prev.replace((value, ts));
                match prev {
                    Some((p, _)) if (p - value).abs() > tolerance => Some(Transition::Raised(Alarm {
                        level: self.level,
                        message: format!("changed from {} to {}", num(p), num(value)),
                        ts,
                    })),
                    _ => None,
                }
            }
            _ => self.latch(ts, value),
        }
    }

    fn condition(&mut self, value: f64) -> Option<String> {
        match &mut self.kind {
            Kind::HighLimit { limit, deadband } => {
                let edge = if self.raised_at.is_some() { *limit - *deadband } else { *limit };
                (value > edge).then(|| format!("{} > {}", num(value), num(*limit)))
            }
            Kind::LowLimit { limit, deadband } => {
                let edge = if self.raised_at.is_some() { *limit + *deadband } else { *limit };
                (value < edge).then(|| format!("{} < {}", num(value), num(*limit)))
            }
            Kind::RateOfChange { per_second } => {
                let prev = self.prev.replace((value, 0.0));
                let (p, t) = prev?;
                let now = 0.0;
                let dt = now - t;
                if dt <= 0.0 {
                    return None;
                }
                let rate = (value - p).abs() / dt;
                (rate > *per_second).then(|| format!("changing {rate:.2}/s > {}/s", num(*per_second)))
            }
            Kind::State { states, labels } => states.contains(&value).then(|| {
                let label = labels
                    .iter()
                    .find(|(k, _)| (*k - value).abs() < f64::EPSILON)
                    .map(|(_, l)| l.clone())
                    .unwrap_or_else(|| num(value));
                format!("state is {label}")
            }),
            Kind::Range { low, high, inside } => {
                let within = *low <= value && value <= *high;
                if *inside {
                    within.then(|| format!("{} is inside {} to {}", num(value), num(*low), num(*high)))
                } else {
                    (!within).then(|| format!("{} is outside {} to {}", num(value), num(*low), num(*high)))
                }
            }
            Kind::Smoothness { limit, .. } => {
                let w = self.window.as_mut()?;
                w.push(value);
                if !w.is_full() {
                    return None;
                }
                let sd = w.stdev();
                let n = w.len();
                (sd > *limit).then(|| format!("scatter {sd:.3} > {} over {n} samples", num(*limit)))
            }
            Kind::Cusum { target, slack, threshold } => {
                self.cusum.0 = (self.cusum.0 + value - *target - *slack).max(0.0);
                self.cusum.1 = (self.cusum.1 + *target - value - *slack).max(0.0);
                if self.cusum.0 > *threshold {
                    Some(format!("drifting above {} (sum {:.3} > {})", num(*target), self.cusum.0, num(*threshold)))
                } else if self.cusum.1 > *threshold {
                    Some(format!("drifting below {} (sum {:.3} > {})", num(*target), self.cusum.1, num(*threshold)))
                } else {
                    None
                }
            }
            _ => None,
        }
    }

    /// Rate of change needs the timestamp, which the latching path does not otherwise carry. Kept separate so
    /// the common family stays a plain comparison.
    fn latch(&mut self, ts: f64, value: f64) -> Option<Transition> {
        if matches!(self.kind, Kind::RateOfChange { .. }) {
            if let Some((_, t)) = self.prev {
                if ts <= t {
                    return None;  // same instant, no interval: neither a rise nor a return to normal
                }
            }
            let prev = self.prev.replace((value, ts));
            let msg = match prev {
                Some((p, t)) if ts > t => {
                    let rate = (value - p).abs() / (ts - t);
                    match &self.kind {
                        Kind::RateOfChange { per_second } if rate > *per_second => {
                            Some(format!("changing {rate:.2}/s > {}/s", num(*per_second)))
                        }
                        _ => None,
                    }
                }
                _ => None,
            };
            return self.apply(ts, msg);
        }
        let msg = self.condition(value);
        self.apply(ts, msg)
    }

    /// The shared latch of the Python `_Latch`: on-delay before raising, off-delay before clearing.
    fn apply(&mut self, ts: f64, msg: Option<String>) -> Option<Transition> {
        let Some(msg) = msg else {
            if self.raised_at.is_some() && self.off_delay > 0.0 {
                let since = *self.ok_since.get_or_insert(ts);
                if ts - since < self.off_delay {
                    return None;
                }
            }
            self.since = None;
            self.ok_since = None;
            if let Some(raised_at) = self.raised_at.take() {
                return Some(Transition::Cleared { raised_at, cleared_at: ts });
            }
            return None;
        };
        self.ok_since = None;
        let since = *self.since.get_or_insert(ts);
        if self.raised_at.is_none() && ts - since >= self.duration {
            self.raised_at = Some(ts);
            return Some(Transition::Raised(Alarm { level: self.level, message: msg, ts }));
        }
        None
    }

    /// Time passed, maybe with no value: drives NO_UPDATE and NO_CHANGE, which cannot fire on a value.
    pub fn tick(&mut self, now: f64, last_recv: f64) -> Option<Transition> {
        if !now.is_finite() || !last_recv.is_finite() {
            return None;
        }
        match self.kind {
            Kind::NoUpdate { seconds } => {
                if self.raised_at.is_none() && now - last_recv > seconds {
                    self.raised_at = Some(now);
                    return Some(Transition::Raised(Alarm {
                        level: self.level,
                        message: format!("no update for {:.0}s", now - last_recv),
                        ts: now,
                    }));
                }
                None
            }
            Kind::NoChange { seconds, .. } => {
                let (_, changed) = self.still?;
                if self.raised_at.is_none() && now - changed > seconds {
                    self.raised_at = Some(now);
                    return Some(Transition::Raised(Alarm {
                        level: self.level,
                        message: format!("value has not changed for {:.0}s", now - changed),
                        ts: now,
                    }));
                }
                None
            }
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn det(kind: Kind) -> Detector {
        Detector::new(kind, Level::Warning)
    }

    fn hi(limit: f64) -> Kind {
        Kind::HighLimit { limit, deadband: 0.0 }
    }

    #[test]
    fn high_limit_raises_once_and_clears() {
        let mut d = det(hi(10.0));
        assert_eq!(d.check(1.0, 5.0), None);
        let raised = d.check(2.0, 11.0).expect("raised");
        assert!(matches!(&raised, Transition::Raised(a) if a.message == "11 > 10"));
        assert_eq!(d.check(3.0, 12.0), None, "does not raise twice");
        assert_eq!(d.check(4.0, 9.0), Some(Transition::Cleared { raised_at: 2.0, cleared_at: 4.0 }));
    }

    #[test]
    fn deadband_holds_the_alarm_until_the_value_passes_it() {
        let mut d = det(Kind::HighLimit { limit: 10.0, deadband: 2.0 });
        assert!(d.check(1.0, 11.0).is_some());
        assert_eq!(d.check(2.0, 9.5), None, "inside the band: still raised");
        assert!(matches!(d.check(3.0, 7.9), Some(Transition::Cleared { .. })), "below limit - deadband: clears");
    }

    #[test]
    fn on_delay_needs_the_condition_to_hold() {
        let mut d = Detector::with_delays(hi(10.0), Level::Warning, 5.0, 0.0);
        assert_eq!(d.check(0.0, 11.0), None);
        assert_eq!(d.check(4.0, 11.0), None, "not long enough");
        assert!(d.check(5.0, 11.0).is_some(), "held for 5s");
    }

    #[test]
    fn off_delay_waits_before_clearing() {
        let mut d = Detector::with_delays(hi(10.0), Level::Warning, 0.0, 4.0);
        assert!(d.check(0.0, 11.0).is_some());
        assert_eq!(d.check(1.0, 5.0), None, "normal, but not for long enough");
        assert_eq!(d.check(2.0, 11.0), None, "relapse restarts nothing: still raised");
        assert_eq!(d.check(3.0, 5.0), None);
        assert_eq!(d.check(7.0, 5.0), Some(Transition::Cleared { raised_at: 0.0, cleared_at: 7.0 }));
    }

    #[test]
    fn a_relapse_restarts_the_off_delay_wait() {
        let mut d = Detector::with_delays(hi(10.0), Level::Warning, 0.0, 4.0);
        assert!(d.check(0.0, 11.0).is_some());
        assert_eq!(d.check(1.0, 5.0), None);
        assert_eq!(d.check(2.0, 11.0), None, "relapse");
        assert_eq!(d.check(3.0, 5.0), None, "wait restarts here");
        assert_eq!(d.check(6.0, 5.0), None);
        assert!(d.check(7.0, 5.0).is_some(), "4s after the relapse");
    }

    #[test]
    fn low_limit() {
        let mut d = det(Kind::LowLimit { limit: 2.0, deadband: 0.0 });
        assert!(matches!(d.check(1.0, 1.0), Some(Transition::Raised(_))));
        assert!(matches!(d.check(2.0, 3.0), Some(Transition::Cleared { .. })));
    }

    #[test]
    fn rate_of_change_uses_the_real_interval() {
        let mut d = det(Kind::RateOfChange { per_second: 2.0 });
        d.check(0.0, 0.0);
        assert_eq!(d.check(1.0, 1.0), None, "1/s");
        assert!(matches!(d.check(2.0, 5.0), Some(Transition::Raised(_))), "4/s");
        assert_eq!(d.check(2.0, 9.0), None, "a zero interval cannot poison the rate");
    }

    #[test]
    fn no_update_fires_on_our_clock_and_clears_on_a_value() {
        let mut d = det(Kind::NoUpdate { seconds: 60.0 });
        assert_eq!(d.tick(10.0, 10.0), None);
        assert!(matches!(d.tick(100.0, 10.0), Some(Transition::Raised(_))));
        assert!(matches!(d.check(101.0, 1.0), Some(Transition::Cleared { raised_at: 100.0, cleared_at: 101.0 })));
        assert_eq!(d.tick(150.0, 101.0), None, "49s since the last value: still inside the window");
        assert!(matches!(d.tick(200.0, 101.0), Some(Transition::Raised(_))), "99s: it fires again");
    }

    #[test]
    fn no_change_needs_updates_that_do_not_move() {
        let mut d = det(Kind::NoChange { seconds: 5.0, tolerance: 0.1 });
        d.check(0.0, 1.0);
        assert_eq!(d.tick(4.0, 4.0), None);
        assert!(matches!(d.tick(6.0, 6.0), Some(Transition::Raised(_))));
        assert!(d.check(7.0, 2.0).is_some(), "a real move clears it");
    }

    #[test]
    fn smoothness_needs_a_full_window() {
        let mut d = det(Kind::Smoothness { limit: 0.5, samples: 4 });
        for (i, v) in [1.0, 1.0, 1.0].iter().enumerate() {
            assert_eq!(d.check(i as f64, *v), None);
        }
        assert!(matches!(d.check(3.0, 9.0), Some(Transition::Raised(_))), "the window is full and scatters");
    }

    #[test]
    fn the_window_never_grows() {
        let mut d = det(Kind::Smoothness { limit: 1e9, samples: 8 });
        for i in 0..10_000 {
            d.check(i as f64, 1.0);
        }
        let w = d.window.as_ref().unwrap();
        assert_eq!(w.len(), 8);
        assert_eq!(w.buf.len(), 8);
    }

    #[test]
    fn cusum_catches_a_small_steady_offset() {
        let mut d = det(Kind::Cusum { target: 10.0, slack: 0.0, threshold: 5.0 });
        let mut raised = None;
        for i in 0..10 {
            if let Some(t) = d.check(i as f64, 12.0) {
                raised = Some(t);
                break;
            }
        }
        assert!(matches!(raised, Some(Transition::Raised(_))));
    }

    #[test]
    fn state_uses_the_labels_it_was_given() {
        let mut d = det(Kind::State { states: vec![2.0], labels: vec![(2.0, "Fault".into())] });
        assert!(matches!(d.check(1.0, 2.0), Some(Transition::Raised(a)) if a.message == "state is Fault"));
    }

    #[test]
    fn range_inside_and_outside() {
        let mut out = det(Kind::Range { low: 0.0, high: 10.0, inside: false });
        assert!(out.check(1.0, 11.0).is_some());
        let mut inb = det(Kind::Range { low: 0.0, high: 10.0, inside: true });
        assert!(inb.check(1.0, 5.0).is_some());
        assert_eq!(inb.check(2.0, 11.0), Some(Transition::Cleared { raised_at: 1.0, cleared_at: 2.0 }));
    }

    #[test]
    fn change_is_momentary() {
        let mut d = det(Kind::Change { tolerance: 0.0 });
        assert_eq!(d.check(1.0, 1.0), None);
        assert!(matches!(d.check(2.0, 2.0), Some(Transition::Raised(_))));
        assert!(!d.is_active(), "a change event needs no clear");
    }

    #[test]
    fn non_finite_values_never_reach_a_comparison() {
        let mut d = det(hi(10.0));
        assert_eq!(d.check(1.0, f64::NAN), None);
        assert_eq!(d.check(2.0, f64::INFINITY), None);
        assert_eq!(d.rejected, 2);
        assert!(!d.is_active(), "NaN is neither above nor below a limit");
        assert!(d.check(3.0, 11.0).is_some(), "and the detector still works afterwards");
    }

    #[test]
    fn a_bad_sample_count_is_refused_at_construction() {
        let cfg = |v: &str| match v {
            "limit" => Some(1.0),
            "samples" => Some(0.0),
            _ => None,
        };
        assert!(Kind::from_config("SMOOTHNESS", &cfg).is_err());
        let huge = |v: &str| match v {
            "limit" => Some(1.0),
            "samples" => Some((MAX_SAMPLES + 1) as f64),
            _ => None,
        };
        assert!(Kind::from_config("SMOOTHNESS", &huge).is_err(), "no unbounded window");
    }

    #[test]
    fn reset_forgets_an_alarm_without_reporting_a_clear() {
        let mut d = det(hi(10.0));
        assert!(d.check(1.0, 11.0).is_some());
        d.reset();
        assert!(!d.is_active());
        assert!(d.check(2.0, 11.0).is_some(), "it can raise again");
    }

    #[test]
    fn ring_wraps_in_order() {
        let mut r = Ring::new(3);
        for v in [1.0, 2.0, 3.0, 4.0] {
            r.push(v);
        }
        assert_eq!(r.iter().collect::<Vec<_>>(), vec![2.0, 3.0, 4.0]);
    }
}
