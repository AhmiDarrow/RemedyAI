// On Windows, stamp the 0.70.5 multi-size icon.ico into the exe resource
// and request elevation (requireAdministrator), matching 0.70.5's start
// as admin. The NSIS installer stays per-user (`RequestExecutionLevel user`)
// so silent upgrades of %LOCALAPPDATA% still work; the app itself then
// elevates when launched. Other hosts ignore this; the tray image is set
// in src/lib.rs from tray-icon.png.

fn main() {
    let ico = std::path::Path::new("../../src-tauri/icons/icon.ico");
    let manifest = std::path::Path::new("packaging/windows/remedy-desktop.exe.manifest");
    println!("cargo:rerun-if-changed={}", ico.display());
    println!("cargo:rerun-if-changed={}", manifest.display());
    println!("cargo:rerun-if-changed=../../src-tauri/icons/tray-icon.png");
    #[cfg(windows)]
    {
        let mut res = winresource::WindowsResource::new();
        if ico.is_file() {
            res.set_icon("../../src-tauri/icons/icon.ico");
        }
        if manifest.is_file() {
            res.set_manifest_file("packaging/windows/remedy-desktop.exe.manifest");
        } else {
            res.set_manifest(
                r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="requireAdministrator" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
</assembly>
"#,
            );
        }
        res.compile()
            .expect("embed Windows icon and elevation manifest");
    }
}
