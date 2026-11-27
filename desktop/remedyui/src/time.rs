//! Relative timestamps for session rows, stable when `now` is supplied (previews).

use std::time::{SystemTime, UNIX_EPOCH};

/// Formats `rfc3339` as a short age relative to `now_secs` (Unix seconds).
pub fn relative(rfc3339: &str, now_secs: i64) -> String {
    let Some(then) = parse_rfc3339(rfc3339) else {
        return String::new();
    };
    let delta = (now_secs - then).max(0);
    if delta < 45 {
        "just now".into()
    } else if delta < 90 {
        "1m ago".into()
    } else if delta < 60 * 45 {
        format!("{}m ago", ((delta as f64) / 60.0).round() as i64)
    } else if delta < 60 * 90 {
        "1h ago".into()
    } else if delta < 60 * 60 * 22 {
        format!("{}h ago", ((delta as f64) / 3600.0).round() as i64)
    } else if delta < 60 * 60 * 36 {
        "1d ago".into()
    } else {
        format!("{}d ago", ((delta as f64) / 86400.0).round() as i64)
    }
}

/// Uppercase day stamp for the chat feed, e.g. `SUN, NOV 22`.
pub fn day_label(rfc3339: &str) -> String {
    let s = rfc3339.trim();
    let date = s.split(['T', ' ']).next().unwrap_or(s);
    let mut d = date.split('-');
    let Some(y) = d.next().and_then(|v| v.parse::<i64>().ok()) else {
        return String::new();
    };
    let Some(mo) = d.next().and_then(|v| v.parse::<i64>().ok()) else {
        return String::new();
    };
    let Some(day) = d.next().and_then(|v| v.parse::<i64>().ok()) else {
        return String::new();
    };
    if !(1..=12).contains(&mo) || !(1..=31).contains(&day) {
        return String::new();
    }
    let Some(secs) = parse_rfc3339(&format!("{y:04}-{mo:02}-{day:02}T00:00:00Z")) else {
        return String::new();
    };
    let days = secs.div_euclid(86400);
    // 1970-01-01 was a Thursday. Index 0 = Monday.
    let wday = ((days + 3).rem_euclid(7)) as usize;
    const WDAYS: [&str; 7] = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
    const MONTHS: [&str; 12] = [
        "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    ];
    format!("{}, {} {}", WDAYS[wday], MONTHS[(mo - 1) as usize], day)
}

/// A clock time from seconds on the local wall clock, e.g. `2:14 PM`.
pub fn clock(local_seconds: i64) -> String {
    let day = local_seconds.rem_euclid(86_400);
    let (h, m) = (day / 3600, day % 3600 / 60);
    let (h12, half) = match h {
        0 => (12, "AM"),
        1..=11 => (h, "AM"),
        12 => (12, "PM"),
        _ => (h - 12, "PM"),
    };
    format!("{h12}:{m:02} {half}")
}

/// Unix seconds as `2026-11-22T20:00:00Z`, the form the runtime stores.
pub fn rfc3339(unix_seconds: i64) -> String {
    let days = unix_seconds.div_euclid(86_400);
    let secs = unix_seconds.rem_euclid(86_400);
    // Days to civil date (Gregorian).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z", secs / 3600, secs % 3600 / 60, secs % 60)
}

/// Unix seconds now.
pub fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

pub(crate) fn parse_rfc3339(s: &str) -> Option<i64> {
    // 2026-11-22T20:00:00Z or 2026-11-22T20:00:00.000Z
    let s = s.trim().trim_end_matches('Z');
    let (date, time) = s.split_once('T')?;
    let mut d = date.split('-');
    let y: i64 = d.next()?.parse().ok()?;
    let mo: i64 = d.next()?.parse().ok()?;
    let day: i64 = d.next()?.parse().ok()?;
    let time = time.split('.').next().unwrap_or(time);
    let mut t = time.split(':');
    let h: i64 = t.next()?.parse().ok()?;
    let mi: i64 = t.next()?.parse().ok()?;
    let se: i64 = t.next()?.parse().ok()?;
    // Civil to Unix, UTC, Gregorian (adequate for session ages).
    let mut y = y;
    let mut mo = mo;
    if mo <= 2 {
        y -= 1;
        mo += 12;
    }
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (mo - 3) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some((era * 146097 + doe - 719468) * 86400 + h * 3600 + mi * 60 + se)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn buckets_hours_and_days() {
        let now = parse_rfc3339("2026-11-22T21:00:00Z").unwrap();
        assert_eq!(
            now, 1_795_381_200,
            "civil conversion must match real Unix time"
        );
        assert_eq!(relative("2026-11-22T20:00:00Z", now), "1h ago");
        assert_eq!(relative("2026-11-22T09:15:00Z", now), "12h ago");
        assert_eq!(relative("2026-11-22T20:59:30Z", now), "just now");
        assert_eq!(
            relative("2026-11-22T20:00:00Z", 1_795_381_200),
            "1h ago",
            "preview clock and parser stay in lockstep"
        );
    }

    #[test]
    fn clock_and_stored_form() {
        assert_eq!(clock(0), "12:00 AM");
        assert_eq!(clock(13 * 3600 + 5 * 60), "1:05 PM");
        assert_eq!(clock(12 * 3600), "12:00 PM");
        assert_eq!(clock(-60), "11:59 PM");
        // The stored form reads back as the same instant.
        for t in [0, 1_795_381_200, 951_782_399, 951_782_400, 1_709_251_199] {
            assert_eq!(parse_rfc3339(&rfc3339(t)), Some(t), "{t}");
        }
        assert_eq!(rfc3339(1_795_381_200), "2026-11-22T21:00:00Z");
    }

    #[test]
    fn day_stamp_is_uppercase_and_stable() {
        assert_eq!(day_label("2026-11-22T15:00:00Z"), "SUN, NOV 22");
        assert_eq!(day_label("1970-01-01T00:00:00Z"), "THU, JAN 1");
        assert_eq!(day_label(""), "");
    }
}