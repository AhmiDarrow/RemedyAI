//! Device X25519 identity, device id, and key/pairing persistence.

use std::fmt;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use snow::params::DHChoice;
use snow::resolvers::{CryptoResolver, DefaultResolver};
use zeroize::{Zeroize, ZeroizeOnDrop, Zeroizing};

use crate::error::{Error, Result};
use crate::pairing::PairingRecord;

pub const KEY_LEN: usize = 32;

/// The phone's static X25519 key. The secret half is zeroized on drop and is
/// never printed; there is deliberately no `Clone`.
#[derive(Zeroize, ZeroizeOnDrop)]
pub struct DeviceKeys {
    secret: [u8; KEY_LEN],
    #[zeroize(skip)]
    public: [u8; KEY_LEN],
}

impl DeviceKeys {
    pub fn generate() -> Result<DeviceKeys> {
        let mut seed = Zeroizing::new([0u8; KEY_LEN]);
        getrandom::fill(&mut seed[..]).map_err(|_| Error::Store("no system randomness".into()))?;
        Self::from_secret(&seed)
    }

    pub fn from_secret(secret: &[u8; KEY_LEN]) -> Result<DeviceKeys> {
        let mut dh = DefaultResolver::default()
            .resolve_dh(&DHChoice::Curve25519)
            .ok_or(Error::Noise("X25519 unavailable"))?;
        dh.set(secret);
        let mut public = [0u8; KEY_LEN];
        public.copy_from_slice(dh.pubkey());
        Ok(DeviceKeys { secret: *secret, public })
    }

    pub fn public(&self) -> &[u8; KEY_LEN] {
        &self.public
    }

    pub fn device_id(&self) -> String {
        device_id_of(&self.public)
    }

    pub(crate) fn secret(&self) -> &[u8; KEY_LEN] {
        &self.secret
    }
}

impl fmt::Debug for DeviceKeys {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "DeviceKeys(id={}, secret=<redacted>)", self.device_id())
    }
}

/// First 32 hex characters of SHA-256 of the device public key.
pub fn device_id_of(public: &[u8; KEY_LEN]) -> String {
    let digest = Sha256::digest(public);
    hex::encode(digest)[..32].to_string()
}

/// Where the phone keeps its identity and its pairing.
pub trait KeyStore: Send + Sync {
    fn load_device_keys(&self) -> Result<Option<DeviceKeys>>;
    fn save_device_keys(&self, keys: &DeviceKeys) -> Result<()>;
    fn load_pairing(&self) -> Result<Option<PairingRecord>>;
    fn save_pairing(&self, record: &PairingRecord) -> Result<()>;
    /// Forget the pairing only; the device key stays.
    fn clear_pairing(&self) -> Result<()>;
}

/// Load the device key, creating and saving one on first use.
pub fn load_or_create_device_keys(store: &dyn KeyStore) -> Result<DeviceKeys> {
    if let Some(k) = store.load_device_keys()? {
        return Ok(k);
    }
    let k = DeviceKeys::generate()?;
    store.save_device_keys(&k)?;
    Ok(k)
}

// ---- in-memory store -------------------------------------------------------

#[derive(Default)]
pub struct MemoryKeyStore {
    keys: Mutex<Option<Zeroizing<[u8; KEY_LEN]>>>,
    pairing: Mutex<Option<PairingRecord>>,
}

impl MemoryKeyStore {
    pub fn new() -> Self {
        Self::default()
    }
}

impl KeyStore for MemoryKeyStore {
    fn load_device_keys(&self) -> Result<Option<DeviceKeys>> {
        match &*self.keys.lock().unwrap() {
            Some(s) => Ok(Some(DeviceKeys::from_secret(s)?)),
            None => Ok(None),
        }
    }
    fn save_device_keys(&self, keys: &DeviceKeys) -> Result<()> {
        *self.keys.lock().unwrap() = Some(Zeroizing::new(*keys.secret()));
        Ok(())
    }
    fn load_pairing(&self) -> Result<Option<PairingRecord>> {
        Ok(self.pairing.lock().unwrap().clone())
    }
    fn save_pairing(&self, record: &PairingRecord) -> Result<()> {
        *self.pairing.lock().unwrap() = Some(record.clone());
        Ok(())
    }
    fn clear_pairing(&self) -> Result<()> {
        *self.pairing.lock().unwrap() = None;
        Ok(())
    }
}

// ---- file store ------------------------------------------------------------

#[derive(Serialize, Deserialize, Zeroize, ZeroizeOnDrop)]
struct FileDoc {
    version: u32,
    /// hex of the 32-byte X25519 secret
    device_secret: Option<String>,
    #[zeroize(skip)]
    pairing: Option<PairingRecord>,
}

/// One JSON file, written atomically (temp file + rename). On Unix the file
/// is created with mode 0600; on other systems it inherits the permissions of
/// its directory (the app's private storage on Android and Windows profiles).
pub struct FileKeyStore {
    path: PathBuf,
    lock: Mutex<()>,
}

impl FileKeyStore {
    pub fn new(path: impl Into<PathBuf>) -> FileKeyStore {
        FileKeyStore { path: path.into(), lock: Mutex::new(()) }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn read(&self) -> Result<FileDoc> {
        match fs::read(&self.path) {
            Ok(bytes) => {
                let bytes = Zeroizing::new(bytes);
                let doc: FileDoc =
                    serde_json::from_slice(&bytes).map_err(|_| Error::Store("key file is not valid JSON".into()))?;
                if doc.version != 1 {
                    return Err(Error::Store("key file has an unknown version".into()));
                }
                Ok(doc)
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                Ok(FileDoc { version: 1, device_secret: None, pairing: None })
            }
            Err(e) => Err(Error::Store(format!("cannot read key file: {}", e.kind()))),
        }
    }

    fn write(&self, doc: &FileDoc) -> Result<()> {
        let bytes = Zeroizing::new(
            serde_json::to_vec_pretty(doc).map_err(|_| Error::Store("cannot encode key file".into()))?,
        );
        let dir = self.path.parent().filter(|p| !p.as_os_str().is_empty());
        if let Some(d) = dir {
            fs::create_dir_all(d).map_err(|e| Error::Store(format!("cannot create key dir: {}", e.kind())))?;
        }
        let mut tmp = self.path.clone().into_os_string();
        tmp.push(".tmp");
        let tmp = PathBuf::from(tmp);
        let res = (|| -> std::io::Result<()> {
            let mut opts = fs::OpenOptions::new();
            opts.write(true).create(true).truncate(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                opts.mode(0o600);
            }
            let mut f = opts.open(&tmp)?;
            f.write_all(&bytes)?;
            f.sync_all()?;
            drop(f);
            fs::rename(&tmp, &self.path)
        })();
        if let Err(e) = res {
            let _ = fs::remove_file(&tmp);
            return Err(Error::Store(format!("cannot write key file: {}", e.kind())));
        }
        Ok(())
    }
}

impl KeyStore for FileKeyStore {
    fn load_device_keys(&self) -> Result<Option<DeviceKeys>> {
        let _g = self.lock.lock().unwrap();
        let doc = self.read()?;
        let Some(h) = &doc.device_secret else { return Ok(None) };
        let raw = Zeroizing::new(hex::decode(h).map_err(|_| Error::Store("device key is corrupt".into()))?);
        let secret = <[u8; KEY_LEN]>::try_from(raw.as_slice())
            .map_err(|_| Error::Store("device key is corrupt".into()))?;
        let secret = Zeroizing::new(secret);
        Ok(Some(DeviceKeys::from_secret(&secret)?))
    }

    fn save_device_keys(&self, keys: &DeviceKeys) -> Result<()> {
        let _g = self.lock.lock().unwrap();
        let mut doc = self.read()?;
        doc.device_secret = Some(hex::encode(keys.secret()));
        self.write(&doc)
    }

    fn load_pairing(&self) -> Result<Option<PairingRecord>> {
        let _g = self.lock.lock().unwrap();
        Ok(self.read()?.pairing.clone())
    }

    fn save_pairing(&self, record: &PairingRecord) -> Result<()> {
        let _g = self.lock.lock().unwrap();
        let mut doc = self.read()?;
        doc.pairing = Some(record.clone());
        self.write(&doc)
    }

    fn clear_pairing(&self) -> Result<()> {
        let _g = self.lock.lock().unwrap();
        let mut doc = self.read()?;
        doc.pairing = None;
        self.write(&doc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pairing::PairingRecord;
    use std::collections::BTreeMap;

    fn tmp(name: &str) -> PathBuf {
        let mut p = std::env::temp_dir();
        p.push(format!("remedy-connect-core-{}-{}-{}", name, std::process::id(), crate::util::unix_now()));
        let _ = fs::remove_dir_all(&p);
        p
    }

    fn record() -> PairingRecord {
        PairingRecord {
            host_key: [9; 32],
            lan: "192.168.1.5:7401".parse().unwrap(),
            tailscale: Some("100.64.0.1:7401".parse().unwrap()),
            v6: None,
            device_name: "pixel".into(),
            paired_at: 1,
            extras: BTreeMap::new(),
        }
    }

    #[test]
    fn public_key_and_device_id_vectors() {
        // Fixture key from tests/fixtures/connect/noise_ik_pair_secret.json.
        let k = DeviceKeys::from_secret(&[0x11; 32]).unwrap();
        assert_eq!(hex::encode(k.public()), "7b4e909bbe7ffe44c465a220037d608ee35897d31ef972f07f74892cb0f73f13");
        let id = k.device_id();
        assert_eq!(id.len(), 32);
        assert_eq!(id, hex::encode(Sha256::digest(k.public()))[..32]);
        let r = DeviceKeys::from_secret(&[0x22; 32]).unwrap();
        assert_eq!(hex::encode(r.public()), "0faa684ed28867b97f4a6a2dee5df8ce974e76b7018e3f22a1c4cf2678570f20");
    }

    #[test]
    fn debug_hides_secret() {
        let k = DeviceKeys::from_secret(&[0x11; 32]).unwrap();
        let d = format!("{k:?}");
        assert!(d.contains("redacted"));
        assert!(!d.contains(&hex::encode([0x11u8; 32])) && !d.contains("17, 17"));
    }

    #[test]
    fn generate_is_random() {
        let a = DeviceKeys::generate().unwrap();
        let b = DeviceKeys::generate().unwrap();
        assert_ne!(a.public(), b.public());
    }

    #[test]
    fn file_store_roundtrip_and_independent_parts() {
        let dir = tmp("store");
        let store = FileKeyStore::new(dir.join("connect.json"));
        assert!(store.load_device_keys().unwrap().is_none());
        assert!(store.load_pairing().unwrap().is_none());
        let keys = load_or_create_device_keys(&store).unwrap();
        let again = load_or_create_device_keys(&store).unwrap();
        assert_eq!(keys.public(), again.public());
        store.save_pairing(&record()).unwrap();
        assert_eq!(store.load_pairing().unwrap().unwrap(), record());
        assert_eq!(store.load_device_keys().unwrap().unwrap().public(), keys.public());
        store.clear_pairing().unwrap();
        assert!(store.load_pairing().unwrap().is_none());
        assert!(store.load_device_keys().unwrap().is_some());
        assert!(!dir.join("connect.json.tmp").exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tmp("perm");
        let store = FileKeyStore::new(dir.join("connect.json"));
        load_or_create_device_keys(&store).unwrap();
        let mode = fs::metadata(store.path()).unwrap().permissions().mode();
        assert_eq!(mode & 0o077, 0, "mode {mode:o}");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn corrupt_files_fail_closed() {
        let dir = tmp("corrupt");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("connect.json");
        let store = FileKeyStore::new(&path);
        fs::write(&path, b"not json").unwrap();
        assert!(matches!(store.load_device_keys(), Err(Error::Store(_))));
        fs::write(&path, br#"{"version":1,"device_secret":"abcd","pairing":null}"#).unwrap();
        assert!(matches!(store.load_device_keys(), Err(Error::Store(_))));
        fs::write(&path, br#"{"version":2,"device_secret":null,"pairing":null}"#).unwrap();
        assert!(matches!(store.load_pairing(), Err(Error::Store(_))));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn memory_store_matches_contract() {
        let s = MemoryKeyStore::new();
        let k = load_or_create_device_keys(&s).unwrap();
        assert_eq!(load_or_create_device_keys(&s).unwrap().public(), k.public());
        s.save_pairing(&record()).unwrap();
        assert!(s.load_pairing().unwrap().is_some());
        s.clear_pairing().unwrap();
        assert!(s.load_pairing().unwrap().is_none());
    }
}
