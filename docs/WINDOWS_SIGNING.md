# Windows code signing & SmartScreen

Remedy Desktop is distributed as an NSIS `.exe` installer. Without an
**Authenticode** signature from a trusted certificate authority (CA), Windows
SmartScreen shows “Windows protected your PC” / “Unknown publisher” on first
install. That is expected for unsigned builds — not a bug in the app.

## What we already sign

| Artifact | Method | Purpose |
|----------|--------|---------|
| Updater payload (`latest.json` + `.sig`) | **minisign** via Tauri (`TAURI_SIGNING_*` secrets) | In-app auto-update trust |
| Installer EXE | *not yet Authenticode-signed* | OS trust / SmartScreen |

minisign protects **updates between already-installed copies**. It does **not**
replace Authenticode for the **first** download from the browser.

### Publisher identity (verify in-app updates)

| Item | Value |
|------|--------|
| Releases only from | `https://github.com/AhmiDarrow/RemedyAI/releases` |
| Update metadata | `…/releases/latest/download/latest.json` (URL must match publisher) |
| Installer asset name | **`Remedy.Desktop_{X.Y.Z}_x64-setup.exe`** (dots for spaces; never `Remedy_Desktop_`) |
| Example URL | `…/releases/download/v0.14.4/Remedy.Desktop_0.14.4_x64-setup.exe` |
| minisign public key (Tauri base64) | In `desktop/src-tauri/tauri.conf.json` → `plugins.updater.pubkey` |

If auto-update reports `Download URL does not match signed latest.json asset`,
compare the **got** URL filename to the table above. Rename the GitHub Release
asset to the canonical `Remedy.Desktop_*` form (or re-run Desktop Release CI)
so it matches `latest.json` — that is the intended ops fix.

Current embedded pubkey (base64 minisign; also in `tauri.conf.json`):

```
dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEQ2MDEwQzVERTNBQ0JDRTAKUldUZ3ZLempYUXdCMWdRNWl0UzlpSDVUamJQZXRvREFpNE9Mb2xJeGpQck5ubVJ5ZDNxSko0dTYK
```

Install **only** from this repository’s GitHub Releases. In-app updates refuse assets that fail signature or publisher URL checks.

## What removes the SmartScreen warning

1. Buy an **Authenticode code-signing certificate**
   - **OV** (Organization Validation) — standard; SmartScreen reputation builds over time with download volume.
   - **EV** (Extended Validation) — higher cost; often immediate reputation with SmartScreen.
2. Sign the **NSIS setup EXE** (and ideally the main app EXE) after `tauri build`.
3. Optionally timestamp the signature so it remains valid after the cert expires.
4. Publish the signed installer on GitHub Releases (CI).

### Typical vendors

- DigiCert, Sectigo, SSL.com, GlobalSign  
- Hardware token / cloud HSM often required for EV (and increasingly for OV).

### CI sketch (after you have a cert)

```yaml
# After tauri build produces the NSIS setup.exe:
- name: Authenticode sign installer
  run: |
    # Example with signtool + cert from secrets / Azure Key Vault / SSL.com eSigner
    signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f cert.pfx /p "%CERT_PASSWORD%" "path\to\Remedy.Desktop_*_x64-setup.exe"
    signtool verify /pa "path\to\Remedy.Desktop_*_x64-setup.exe"
```

Store the PFX/password or cloud-HSM credentials only as **GitHub Actions secrets**
(never commit them). Wire the step into `.github/workflows/desktop-release.yml`
after the Tauri build job and before `softprops/action-gh-release`.

### Local one-off sign

```powershell
# With Windows SDK signtool and a .pfx:
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /f "$env:USERPROFILE\certs\remedy-codesign.pfx" /p $env:CERT_PASSWORD `
  .\desktop\src-tauri\target\release\bundle\nsis\*.exe
```

## Reputation without EV

Even with OV:

- Publish from a consistent publisher name and domain.
- Keep the same signing cert across releases.
- Download volume gradually improves SmartScreen reputation.
- Host downloads on `github.com/AhmiDarrow/RemedyAI` (already).

## Defender ML false positives (not SmartScreen)

Separate from SmartScreen, **Windows Defender** ML may label unsigned
PyInstaller/Tauri builds as:

- `Trojan:Win32/Wacatac.B!ml`
- `Trojan:Win32/Bearfoos.A!ml`
- (legacy) `Behavior:Win32/Persistence.A!ml` when apps write `HKCU\…\Run`

**Code mitigations already in-tree** (see also `docs/DESKTOP.md`):

| Area | Mitigation |
|------|------------|
| Persistence | Startup folder `.lnk` only; never write Run; delete legacy Run names |
| Scrub implementation | Rust `winreg` + NSIS `DeleteRegValue` (no hidden PowerShell Bypass on launch) |
| Sidecar PE identity | PyInstaller `--version-file` + `--icon` (Company/Product/FileVersion filled) |
| Packing | `--noupx` always |
| Bundle metadata | `publisher`, `copyright`, descriptions in `tauri.conf.json`; Cargo authors/repo |

**What still requires a certificate:** Authenticode on the NSIS installer and
main EXE remains the strongest fix for SmartScreen and many ML FPs. Until then,
submit each release binary to Microsoft WDSI if users report quarantines.

## Related files

| File | Role |
|------|------|
| `desktop/src-tauri/tauri.conf.json` | `plugins.updater.pubkey` (minisign public key) |
| `.github/workflows/desktop-release.yml` | Build + minisign update artifacts |
| `scripts/set_tauri_signing_secrets.py` | Upload minisign private key to GH secrets |
| `docs/DESKTOP.md` | Auto-update overview |

## Uninstall data options (related)

Interactive uninstall (not silent `/UPDATE` updates) prompts for:

- **Configuration** — `~\.remedy\config.toml`, `desktop.json`, `auth\`, …
- **Skills** — `~\.remedy\skills`
- **Full wipe** — entire `~\.remedy` plus known app leftovers so a reinstall is clean

Scripts: `desktop/src-tauri/windows/uninstall_options.ps1`, `uninstall_wipe.ps1`  
(wired from `hooks.nsh`).
