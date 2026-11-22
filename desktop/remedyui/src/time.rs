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
}
