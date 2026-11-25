//! Error type. Messages are fixed text or transport-level facts; they never
//! contain key bytes, pair secrets or plaintext.

use std::io;

use crate::pairing::PairingError;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("{0}")]
    Pairing(#[from] PairingError),
    #[error("This is not the PC you paired with. Unpair on this phone to start over.")]
    PinMismatch,
    #[error("i/o error: {0}")]
    Io(#[from] io::Error),
    #[error("timed out: {0}")]
    Timeout(&'static str),
    /// Handshake or AEAD failure (wrong host key, tampered or replayed record).
    #[error("noise failure: {0}")]
    Noise(&'static str),
    /// A peer sent something the wire format does not allow.
    #[error("protocol error: {0}")]
    Protocol(&'static str),
    /// The handshake completed but the PC closed the session before it
    /// answered: pair secret not accepted, device not allowlisted, or Connect
    /// is paused. The PC does not say which.
    #[error("the PC closed the connection after the handshake (pairing not accepted, device not paired, or Connect paused)")]
    Rejected,
    #[error("{0}")]
    Denied(&'static str),
    #[error("{0}")]
    TooLarge(&'static str),
    #[error("tunnel closed: {0}")]
    Closed(String),
    #[error("not connected to the PC")]
    NotConnected,
    #[error("key store: {0}")]
    Store(String),
    #[error("could not reach the PC ({0})")]
    Unreachable(String),
}

pub type Result<T> = std::result::Result<T, Error>;
