//! Server-sent event frames from remedy-runtime (`event:` plus JSON `data:`).

/// One frame from `/messages/stream` or `stream/attach`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Frame {
    /// The SSE `event:` name, if the server sent one.
    pub event: String,
    /// Payload `type` when the data is JSON, else the event name.
    pub kind: String,
    /// Raw data string (JSON or plain).
    pub data: String,
}

/// Incremental SSE parser: push chunks, pull complete frames.
#[derive(Default)]
pub struct Parser {
    buf: String,
    event: String,
    data: String,
}

impl Parser {
    /// An empty parser.
    pub fn new() -> Self {
        Self::default()
    }

    /// Consumes `chunk` and returns every complete frame in order.
    pub fn push(&mut self, chunk: &str) -> Vec<Frame> {
        self.buf.push_str(chunk);
        let mut out = Vec::new();
        loop {
            let Some(end) = self.buf.find('\n') else {
                break;
            };
            let mut line = self.buf.drain(..=end).collect::<String>();
            if line.ends_with('\n') {
                line.pop();
            }
            if line.ends_with('\r') {
                line.pop();
            }
            if line.is_empty() {
                if !self.data.is_empty() || !self.event.is_empty() {
                    out.push(self.take_frame());
                }
                continue;
            }
            if line.starts_with(':') {
                continue;
            }
            if let Some(v) = line.strip_prefix("event:") {
                self.event = v.trim().to_string();
            } else if let Some(v) = line.strip_prefix("data:") {
                let v = v.strip_prefix(' ').unwrap_or(v);
                if !self.data.is_empty() {
                    self.data.push('\n');
                }
                self.data.push_str(v);
            }
        }
        out
    }

    fn take_frame(&mut self) -> Frame {
        let data = std::mem::take(&mut self.data);
        let event = std::mem::take(&mut self.event);
        let kind = json_type(&data).unwrap_or_else(|| event.clone());
        Frame { event, kind, data }
    }
}

fn json_type(data: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(data).ok()?;
    v.get("type")
        .and_then(|t| t.as_str())
        .map(|s| s.to_string())
}

/// Text carried by a `token` (or `text`) frame.
pub fn token_text(data: &str) -> String {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(data) else {
        return data.to_string();
    };
    v.get("text")
        .or_else(|| v.get("delta"))
        .or_else(|| v.get("content"))
        .and_then(|t| t.as_str())
        .unwrap_or("")
        .to_string()
}

/// Error message from an `error` frame, if any.
pub fn error_text(data: &str) -> String {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(data) else {
        return data.to_string();
    };
    v.get("message")
        .or_else(|| v.get("error"))
        .or_else(|| v.get("detail"))
        .and_then(|t| t.as_str())
        .unwrap_or("the mind hit an error")
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_event_and_type() {
        let mut p = Parser::new();
        let frames = p.push("event: token\ndata: {\"type\":\"token\",\"text\":\"Hi\"}\n\n");
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].kind, "token");
        assert_eq!(token_text(&frames[0].data), "Hi");
    }

    #[test]
    fn accepts_partial_chunks_and_type_only_payloads() {
        let mut p = Parser::new();
        assert!(p.push("data: {\"type\":").is_empty());
        let frames = p.push("\"done\"}\n\n");
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].kind, "done");
    }
}
