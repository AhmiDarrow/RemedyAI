//! RemedyConnect on a desktop window, for working on the phone app without a phone.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    let config = remedy_ui::AppConfig::new("Remedy")
        .edition(remedy_ui::Edition::E2026)
        .app_id("com.remedy.groveconnect.dev");
    if let Err(e) = remedy_ui::run(config, remedy_connect::app) {
        eprintln!("RemedyConnect: {e:?}");
        std::process::exit(1);
    }
}