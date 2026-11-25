//! Grove for a paired phone (RemedyConnect).
//!
//! The phone app is Grove and nothing else: talk with Remedy, see what she is
//! working on, and answer when she asks before acting. It is the same Grove
//! code the desktop shows, laid out in one column, minus everything that
//! needs the PC's own screen (Alongside, Studio, files, the microphone).
//!
//! The app shell (`connect/app`) owns pairing and the encrypted tunnel. It
//! hands this module the address of the tunnel's loopback shim, and from
//! there Grove talks to the PC exactly as it does on the desktop.

use remedy_ui::{App, El, Ui};

use crate::grove::Grove;
use crate::runtime::Client;
use crate::studio::Studio;

/// A live Grove bound to one connection.
#[derive(Clone)]
pub struct PhoneGrove {
    grove: Grove,
    client: Client,
}

impl PhoneGrove {
    /// Opens Grove against `origin`, the tunnel shim's address including its
    /// secret path (`http://127.0.0.1:{port}/{token}`). No bearer token is
    /// sent: the PC's gateway adds its own once the tunnel has proved who the
    /// phone is. `persist` keeps Grove's small prefs file under `REMEDY_HOME`.
    pub fn open(origin: &str, persist: bool) -> Self {
        let client = Client::new(origin, None);
        let studio = Studio::new();
        studio.attach_client(client.clone());
        let grove = Grove::for_phone(studio, persist);
        grove.attach_client(client.clone());
        PhoneGrove { grove, client }
    }

    /// The tunnel to the PC just came up. If the first check failed because
    /// there was no path yet (the app was opened out of reach), ask again, so
    /// the screen does not sit on an old error until the app is restarted.
    pub fn revive(&self) {
        use crate::studio::Conn;
        let studio = &self.grove.studio;
        if matches!(studio.conn.get_untracked(), Ok(Conn::Error(_)) | Ok(Conn::Disconnected)) {
            studio.attach_client(self.client.clone());
            self.grove.attach_client(self.client.clone());
        }
    }

    /// The Grove screen. `app` lets Grove raise system notifications.
    pub fn view(&self, ui: &Ui, app: Option<App>) -> El {
        crate::grove::phone(ui, self.grove.clone(), app)
    }
}
