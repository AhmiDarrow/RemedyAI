//! Remedy Desktop.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    let config = remedy_ui::AppConfig::new("Remedy Desktop")
        .edition(remedy_ui::Edition::E2026)
        .app_id("com.remedy.desktop")
        .single_instance();
    if let Err(e) = remedy_ui::run(config, remedy_desktop::app) {
        eprintln!("Remedy Desktop: {e:?}");
        std::process::exit(1);
    }
}
