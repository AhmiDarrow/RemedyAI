// On Windows, stamp the 0.70.5 multi-size icon.ico into the exe resource.
// The exe manifest is asInvoker: running elevated blocked Win+Shift+S and
// other system shortcuts via UIPI while the app had focus (Snipping Tool
// only worked after clicking out). The NSIS installer stays per-user
// (`RequestExecutionLevel user`) so silent upgrades of %LOCALAPPDATA%
// still work. Other hosts ignore this; the tray image is set in src/lib.rs
// from tray-icon.png. Paths are resolved from CARGO_MANIFEST_DIR so the
// embed still works when cargo's cwd is not the crate directory.

fn main() {
    let manifest_dir = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let ico = manifest_dir.join("../src-tauri/icons/icon.ico");
    let manifest = manifest_dir.join("packaging/windows/remedy-desktop.exe.manifest");
    println!("cargo:rerun-if-changed={}", ico.display());
    println!("cargo:rerun-if-changed={}", manifest.display());
    println!(
        "cargo:rerun-if-changed={}",
        manifest_dir.join("../src-tauri/icons/tray-icon.png").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        manifest_dir.join("../src-tauri/icons/32x32.png").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        manifest_dir.join("../src-tauri/icons/128x128.png").display()
    );
    // `cfg(windows)` here is the machine doing the build. The resource belongs
    // only in a Windows exe, so also ask what is being built: an Android
    // library built on this PC has no resource section to stamp.
    #[cfg(windows)]
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winresource::WindowsResource::new();
        if ico.is_file() {
            res.set_icon(ico.to_str().expect("icon.ico path is valid UTF-8"));
        } else {
            println!(
                "cargo:warning=icon.ico not found at {}, the exe will have no app icon",
                ico.display()
            );
        }
        if manifest.is_file() {
            res.set_manifest_file(manifest.to_str().expect("manifest path is valid UTF-8"));
        } else {
            res.set_manifest(
                r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
</assembly>
"#,
            );
        }
        res.compile()
            .expect("embed Windows icon and manifest");
    }
}
