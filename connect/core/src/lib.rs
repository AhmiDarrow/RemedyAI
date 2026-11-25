//! Phone-side transport of RemedyConnect: the encrypted tunnel a paired phone
//! uses to reach the owner's PC, over plain TCP to a LAN, Tailscale or IPv6
//! address, plus the loopback HTTP shim a WebView talks to.
//!
//! Blocking sockets and threads only; no async runtime, no C dependencies.
//! Relay, rendezvous and iroh fields of a pairing text are parsed and kept but
//! never dialed, and no third-party host is ever contacted.
//!
//! Flow:
//! 1. [`client::pair`] with the scanned text -> a [`pairing::PairingRecord`] saved in a [`keys::KeyStore`].
//! 2. [`client::ConnectClient::start_from_store`] keeps a [`client::Tunnel`] up.
//! 3. [`shim::LoopbackShim::start`] with `client.shim_pipe()`; load `shim.web_url()` in the WebView.

#![forbid(unsafe_code)]

pub mod client;
pub mod clock;
pub mod deny;
pub mod direct;
pub mod error;
pub mod frame;
pub mod keys;
pub mod noise;
pub mod pairing;
pub mod shim;
mod util;

#[cfg(test)]
mod testutil;

pub use client::{pair, ClientConfig, ConnState, ConnectClient, Tunnel, Via};
pub use error::{Error, Result};
pub use keys::{DeviceKeys, FileKeyStore, KeyStore, MemoryKeyStore};
pub use pairing::{PairingInfo, PairingRecord};
pub use shim::{LoopbackShim, ShimConfig};
