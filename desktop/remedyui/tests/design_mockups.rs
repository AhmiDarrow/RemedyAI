//! Writes design-review PNGs when `REMEDY_DESIGN_OUT` is set.
//! Default `cargo test` is a no-op so goldens are not disturbed.

use remedy_ui::devtools::Preview;
use std::path::Path;

fn no_spawn_defaults() {
    let home = std::env::temp_dir().join(format!(
        "remedy-design6-home-{}",
        std::process::id()
    ));
    let _ = std::fs::create_dir_all(&home);
    unsafe {
        std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1");
        std::env::set_var("REMEDY_HOME", &home);
        std::env::set_var("REMEDY_LAYOUT_DIR", home.join("layout"));
    }
}

fn write_shot(
    dir: &Path,
    name: &str,
    build: fn(&remedy_ui::Ui) -> remedy_ui::El,
    width: f32,
    height: f32,
) {
    let preview = Preview::new(name, build)
        .sizes(&[(width, height)])
        .themes(&["forest"]);
    let variation = preview
        .variations()
        .into_iter()
        .next()
        .expect("one variation");
    let snap = preview.render(&variation);
    let bytes = snap.to_png().expect("png");
    let path = dir.join(format!("{name}.png"));
    std::fs::write(&path, bytes).unwrap_or_else(|e| panic!("write {}: {e}", path.display()));
    eprintln!(
        "wrote {} ({} bytes)",
        path.display(),
        std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0)
    );
}

#[test]
fn write_design6_review_shots() {
    let Ok(dir) = std::env::var("REMEDY_DESIGN_OUT") else {
        return;
    };
    no_spawn_defaults();
    let dir = Path::new(&dir);
    let _ = std::fs::create_dir_all(dir);
    write_shot(dir, "home", remedy_desktop::design_home, 1180.0, 740.0);
    write_shot(dir, "grove-handoff", remedy_desktop::design_grove_handoff, 1180.0, 740.0);
    write_shot(dir, "build-chat", remedy_desktop::design_build_chat, 1180.0, 740.0);
    write_shot(dir, "plan-dock", remedy_desktop::design_plan, 1180.0, 740.0);
    write_shot(dir, "settings", remedy_desktop::design_settings, 1180.0, 740.0);
    write_shot(dir, "status-rail", remedy_desktop::design_status_rail, 1180.0, 740.0);
    write_shot(dir, "settings-narrow", remedy_desktop::design_settings, 700.0, 740.0);
    write_shot(dir, "presence-idle", remedy_desktop::design_presence_idle, 560.0, 480.0);
    write_shot(dir, "presence-listening", remedy_desktop::design_presence_listening, 560.0, 480.0);
    write_shot(dir, "presence-thinking", remedy_desktop::design_presence_thinking, 560.0, 480.0);
    write_shot(dir, "presence-working", remedy_desktop::design_presence_working, 560.0, 480.0);
    write_shot(dir, "presence-done", remedy_desktop::design_presence_done, 560.0, 480.0);
    write_shot(dir, "presence-error", remedy_desktop::design_presence_error, 560.0, 480.0);
    write_shot(dir, "concept-star", remedy_desktop::design_concept_star, 1180.0, 520.0);
    write_shot(dir, "concept-wolf", remedy_desktop::design_concept_wolf, 1180.0, 520.0);
    write_shot(dir, "concept-bear", remedy_desktop::design_concept_bear, 1180.0, 520.0);
    write_shot(dir, "home-wolf", remedy_desktop::design_home_wolf, 1180.0, 740.0);
    write_shot(dir, "home-bear", remedy_desktop::design_home_bear, 1180.0, 740.0);
}
