//! Remedy Desktop.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    remedy_desktop::install_panic_hook();
    let mut config = remedy_ui::AppConfig::new("Remedy Desktop")
        .edition(remedy_ui::Edition::E2026)
        .app_id("com.remedy.desktop")
        .single_instance();
    config.renderer = remedy_desktop::prefs::renderer_choice(&remedy_desktop::prefs::DesktopPrefs::load().renderer);
    if let Err(e) = remedy_ui::run(config, remedy_desktop::app) {
        eprintln!("Remedy Desktop: {e:?}");
        std::process::exit(1);
    }
}
