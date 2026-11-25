//! Remedy's voice helper.
//!
//! remedy-runtime starts this program the first time someone speaks or
//! listens, and talks to it over stdin/stdout: one JSON request per line, one
//! JSON reply per line. Models stay loaded between requests, so only the first
//! request after a start pays for loading.
//!
//! Requests (`op`):
//! - `ping`: `{}` -> `{ok, version}`
//! - `speak`: `{engine: "kokoro" | "pocket", dir, text, out, sid?, speed?,
//!   reference?, threads?}` -> `{ok, sample_rate}`; the WAV is written to `out`.
//! - `transcribe`: `{dir, wav, language?, threads?}` -> `{ok, text, language}`
//!
//! Every reply carries the request's `id`. A failure is `{ok: false, error}`.
//! The runtime owns downloads, settings and text clean-up; this program only
//! runs models on files it is pointed at.

use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

use serde::Deserialize;
use serde_json::{json, Value};
use sherpa_onnx::{
    GenerationConfig, OfflineRecognizer, OfflineRecognizerConfig, OfflineTts, OfflineTtsConfig, Wave,
};

#[derive(Deserialize)]
struct Request {
    #[serde(default)]
    id: Value,
    op: String,
    #[serde(default)]
    engine: String,
    #[serde(default)]
    dir: String,
    #[serde(default)]
    text: String,
    #[serde(default)]
    out: String,
    #[serde(default)]
    wav: String,
    #[serde(default)]
    reference: String,
    #[serde(default)]
    language: String,
    #[serde(default)]
    sid: i32,
    #[serde(default)]
    speed: f32,
    #[serde(default)]
    threads: i32,
}

/// Loaded models, by what loaded them. A different folder, engine or language
/// is a different model.
#[derive(Default)]
struct Loaded {
    tts: HashMap<String, OfflineTts>,
    stt: HashMap<String, OfflineRecognizer>,
}

fn main() {
    let stdin = std::io::stdin();
    let mut out = std::io::stdout().lock();
    let mut loaded = Loaded::default();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let reply = match serde_json::from_str::<Request>(&line) {
            Ok(req) => {
                let id = req.id.clone();
                let mut v = match handle(&mut loaded, req) {
                    Ok(v) => v,
                    Err(e) => json!({ "ok": false, "error": e }),
                };
                v["id"] = id;
                v
            }
            Err(e) => json!({ "ok": false, "error": format!("bad request: {e}") }),
        };
        // The runtime closing the pipe is how this program is told to stop.
        if writeln!(out, "{reply}").and_then(|_| out.flush()).is_err() {
            break;
        }
    }
}

fn handle(loaded: &mut Loaded, req: Request) -> Result<Value, String> {
    match req.op.as_str() {
        "ping" => Ok(json!({ "ok": true, "version": env!("CARGO_PKG_VERSION") })),
        "speak" => speak(loaded, &req),
        "transcribe" => transcribe(loaded, &req),
        other => Err(format!("unknown op {other:?}")),
    }
}

fn threads(asked: i32) -> i32 {
    if asked > 0 {
        return asked;
    }
    // Half the cores, at most 8: speech is short work beside a desktop in use.
    let n = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(2);
    (n / 2).clamp(1, 8) as i32
}

/// A file the model needs, as text for the config; an error that names it when absent.
fn need(dir: &Path, name: &str) -> Result<String, String> {
    let p = dir.join(name);
    if p.is_file() || p.is_dir() {
        Ok(p.to_string_lossy().into_owned())
    } else {
        Err(format!("the voice model is incomplete: {name} is missing"))
    }
}

/// The first of `names` that exists (a quantized file when the pack has one).
fn first(dir: &Path, names: &[&str]) -> Result<String, String> {
    names
        .iter()
        .find_map(|n| need(dir, n).ok())
        .ok_or_else(|| format!("the voice model is incomplete: {} is missing", names[names.len() - 1]))
}

fn tts_config(engine: &str, dir: &Path, n: i32) -> Result<OfflineTtsConfig, String> {
    let mut c = OfflineTtsConfig::default();
    c.model.num_threads = n;
    match engine {
        "kokoro" => {
            let k = &mut c.model.kokoro;
            k.model = Some(first(dir, &["model.int8.onnx", "model.onnx"])?);
            k.voices = Some(need(dir, "voices.bin")?);
            k.tokens = Some(need(dir, "tokens.txt")?);
            // Older runtimes read espeak data from here; newer ones ignore it.
            k.data_dir = need(dir, "espeak-ng-data").ok();
            k.dict_dir = need(dir, "dict").ok();
            let lexicons: Vec<String> = ["lexicon-us-en.txt", "lexicon-zh.txt"]
                .iter()
                .filter_map(|n| need(dir, n).ok())
                .collect();
            if !lexicons.is_empty() {
                k.lexicon = Some(lexicons.join(","));
            }
            k.length_scale = 1.0;
        }
        "pocket" => {
            let p = &mut c.model.pocket;
            p.lm_flow = Some(first(dir, &["lm_flow.int8.onnx", "lm_flow.onnx"])?);
            p.lm_main = Some(first(dir, &["lm_main.int8.onnx", "lm_main.onnx"])?);
            p.encoder = Some(need(dir, "encoder.onnx")?);
            p.decoder = Some(first(dir, &["decoder.int8.onnx", "decoder.onnx"])?);
            p.text_conditioner = Some(need(dir, "text_conditioner.onnx")?);
            p.vocab_json = Some(need(dir, "vocab.json")?);
            p.token_scores_json = Some(need(dir, "token_scores.json")?);
        }
        other => return Err(format!("unknown voice engine {other:?}")),
    }
    Ok(c)
}

fn speak(loaded: &mut Loaded, req: &Request) -> Result<Value, String> {
    if req.text.trim().is_empty() {
        return Err("nothing to say".into());
    }
    if req.out.is_empty() {
        return Err("speak needs an out path".into());
    }
    let dir = PathBuf::from(&req.dir);
    let key = format!("{}|{}", req.engine, req.dir);
    if !loaded.tts.contains_key(&key) {
        let config = tts_config(&req.engine, &dir, threads(req.threads))?;
        let tts = OfflineTts::create(&config).ok_or("the voice model did not load")?;
        loaded.tts.insert(key.clone(), tts);
    }
    let tts = &loaded.tts[&key];

    let mut g = GenerationConfig::default();
    g.speed = if req.speed > 0.0 { req.speed } else { 1.0 };
    g.sid = req.sid.clamp(0, (tts.num_speakers() - 1).max(0));
    if req.engine == "pocket" {
        // Pocket speaks in the voice of a reference clip; without one it has no voice.
        let clip = Wave::read(&req.reference)
            .ok_or_else(|| format!("the reference clip could not be read: {}", req.reference))?;
        g.reference_audio = Some(clip.samples().to_vec());
        g.reference_sample_rate = clip.sample_rate();
    }
    let audio = tts
        .generate_with_config(&req.text, &g, None::<fn(&[f32], f32) -> bool>)
        .ok_or("the voice model produced no audio")?;
    if audio.samples().is_empty() {
        return Err("the voice model produced no audio".into());
    }
    if !audio.save(&req.out) {
        return Err(format!("the audio could not be written to {}", req.out));
    }
    Ok(json!({ "ok": true, "sample_rate": audio.sample_rate() }))
}

/// The whisper files in `dir`: `<name>-encoder(.int8).onnx`, its decoder and tokens.
fn whisper_files(dir: &Path) -> Result<(String, String, String), String> {
    let names: Vec<String> = std::fs::read_dir(dir)
        .map_err(|e| format!("the listening model folder could not be read: {e}"))?
        .filter_map(|e| e.ok().map(|e| e.file_name().to_string_lossy().into_owned()))
        .collect();
    let pick = |tail: &str| {
        // Quantized first: about a third of the size and faster on a CPU.
        let int8 = format!("-{tail}.int8.onnx");
        let full = format!("-{tail}.onnx");
        names
            .iter()
            .find(|n| n.ends_with(&int8))
            .or_else(|| names.iter().find(|n| n.ends_with(&full)))
            .map(|n| dir.join(n).to_string_lossy().into_owned())
            .ok_or_else(|| format!("the listening model is incomplete: no {tail} file"))
    };
    let tokens = names
        .iter()
        .find(|n| n.ends_with("-tokens.txt"))
        .map(|n| dir.join(n).to_string_lossy().into_owned())
        .ok_or("the listening model is incomplete: no tokens file")?;
    Ok((pick("encoder")?, pick("decoder")?, tokens))
}

fn transcribe(loaded: &mut Loaded, req: &Request) -> Result<Value, String> {
    let dir = PathBuf::from(&req.dir);
    // Whisper takes its language at load time, so each language is its own entry.
    let key = format!("{}|{}", req.dir, req.language);
    if !loaded.stt.contains_key(&key) {
        let (encoder, decoder, tokens) = whisper_files(&dir)?;
        let mut c = OfflineRecognizerConfig::default();
        c.model_config.whisper.encoder = Some(encoder);
        c.model_config.whisper.decoder = Some(decoder);
        c.model_config.whisper.task = Some("transcribe".into());
        if !req.language.is_empty() {
            c.model_config.whisper.language = Some(req.language.clone());
        }
        c.model_config.tokens = Some(tokens);
        c.model_config.num_threads = threads(req.threads);
        let r = OfflineRecognizer::create(&c).ok_or("the listening model did not load")?;
        loaded.stt.insert(key.clone(), r);
    }
    let recognizer = &loaded.stt[&key];
    let wave = Wave::read(&req.wav).ok_or_else(|| format!("the recording could not be read: {}", req.wav))?;
    let stream = recognizer.create_stream();
    stream.accept_waveform(wave.sample_rate(), wave.samples());
    recognizer.decode(&stream);
    let result = stream.get_result().ok_or("the listening model gave no result")?;
    let language = if result.lang.is_empty() { req.language.clone() } else { result.lang.clone() };
    Ok(json!({ "ok": true, "text": result.text.trim(), "language": language.trim_matches(|c| c == '<' || c == '|' || c == '>') }))
}
