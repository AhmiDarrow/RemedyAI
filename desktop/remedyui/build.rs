// On Windows, stamp the 0.70.5 multi-size icon.ico into the exe resource.
// Other hosts ignore this; the tray image is set in src/lib.rs from tray-icon.png.

fn main() {
    let ico = std::path::Path::new("../../src-tauri/icons/icon.ico");
    println!("cargo:rerun-if-changed={}", ico.display());
    println!("cargo:rerun-if-changed=../../src-tauri/icons/tray-icon.png");
    #[cfg(windows)]
    {
        if ico.is_file() {
            let mut res = winresource::WindowsResource::new();
            res.set_icon("../../src-tauri/icons/icon.ico");
            res.compile().expect("embed icon.ico into the Windows exe");
        }
    }
}
