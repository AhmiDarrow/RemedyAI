//! OS tray labels. Startup-only: the menu is built once when the app launches.
//! English strings stay byte-for-byte the historical tray copy (ASCII "...").
//! Traditional Chinese and unknown languages stay English.

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn GetUserDefaultLocaleName(lpLocaleName: *mut u16, cchLocaleName: i32) -> i32;
}

pub struct TrayCopy {
    pub show: &'static str,
    pub settings: &'static str,
    pub updates: &'static str,
    pub about: &'static str,
    pub quit: &'static str,
    pub tooltip: &'static str,
}

const EN: TrayCopy = TrayCopy {
    show: "Show Remedy",
    settings: "Settings...",
    updates: "Check for updates...",
    about: "About Remedy",
    quit: "Quit Remedy",
    tooltip: "Remedy — left-click to show · right-click for menu",
};

fn is_auto(raw: &str) -> bool {
    let low = raw.trim().to_ascii_lowercase();
    low.is_empty() || low == "auto" || low == "default" || low == "system" || low == "os"
}

/// Map a stored id or OS locale onto a tray language, or "" for English.
fn locale_id(raw: &str) -> &'static str {
    let low = raw.trim().replace('_', "-").to_ascii_lowercase();
    if low.is_empty()
        || low == "auto"
        || low == "default"
        || low == "system"
        || low == "os"
        || low == "en"
        || low.starts_with("en-")
    {
        return "";
    }
    if low == "zh-hant" || low == "zh-tw" || low == "zh-hk" || low == "zh-mo" {
        return "";
    }
    if low == "zh" || low == "zh-cn" || low == "zh-sg" || low == "zh-hans" {
        return "zh-Hans";
    }
    if low == "pt" || low.starts_with("pt-") {
        return "pt";
    }
    match low.split('-').next().unwrap_or("") {
        "es" => "es",
        "fr" => "fr",
        "de" => "de",
        "it" => "it",
        "ru" => "ru",
        "pl" => "pl",
        "ja" => "ja",
        "ko" => "ko",
        _ => "",
    }
}

fn copy_for(id: &str) -> TrayCopy {
    match id {
        "es" => TrayCopy {
            show: "Mostrar Remedy",
            settings: "Ajustes…",
            updates: "Buscar actualizaciones…",
            about: "Acerca de Remedy",
            quit: "Salir de Remedy",
            tooltip: "Remedy — clic izquierdo para mostrar · clic derecho para el menú",
        },
        "fr" => TrayCopy {
            show: "Afficher Remedy",
            settings: "Réglages…",
            updates: "Rechercher des mises à jour…",
            about: "À propos de Remedy",
            quit: "Quitter Remedy",
            tooltip: "Remedy — clic gauche pour afficher · clic droit pour le menu",
        },
        "de" => TrayCopy {
            show: "Remedy anzeigen",
            settings: "Einstellungen…",
            updates: "Nach Updates suchen…",
            about: "Über Remedy",
            quit: "Remedy beenden",
            tooltip: "Remedy — Linksklick zum Anzeigen · Rechtsklick für das Menü",
        },
        "pt" => TrayCopy {
            show: "Mostrar Remedy",
            settings: "Configurações…",
            updates: "Verificar atualizações…",
            about: "Sobre o Remedy",
            quit: "Sair do Remedy",
            tooltip: "Remedy — clique esquerdo para mostrar · clique direito para o menu",
        },
        "it" => TrayCopy {
            show: "Mostra Remedy",
            settings: "Impostazioni…",
            updates: "Controlla aggiornamenti…",
            about: "Informazioni su Remedy",
            quit: "Esci da Remedy",
            tooltip: "Remedy — clic sinistro per mostrare · clic destro per il menu",
        },
        "ru" => TrayCopy {
            show: "Показать Remedy",
            settings: "Настройки…",
            updates: "Проверить обновления…",
            about: "О программе Remedy",
            quit: "Выйти из Remedy",
            tooltip: "Remedy — щелчок левой кнопкой, чтобы показать · правой — меню",
        },
        "pl" => TrayCopy {
            show: "Pokaż Remedy",
            settings: "Ustawienia…",
            updates: "Sprawdź aktualizacje…",
            about: "O programie Remedy",
            quit: "Zakończ Remedy",
            tooltip: "Remedy — lewy przycisk pokazuje · prawy otwiera menu",
        },
        "ja" => TrayCopy {
            show: "Remedy を表示",
            settings: "設定…",
            updates: "アップデートを確認…",
            about: "Remedy について",
            quit: "Remedy を終了",
            tooltip: "Remedy — 左クリックで表示 · 右クリックでメニュー",
        },
        "ko" => TrayCopy {
            show: "Remedy 표시",
            settings: "설정…",
            updates: "업데이트 확인…",
            about: "Remedy 정보",
            quit: "Remedy 종료",
            tooltip: "Remedy — 왼쪽 클릭으로 표시 · 오른쪽 클릭으로 메뉴",
        },
        "zh-Hans" => TrayCopy {
            show: "显示 Remedy",
            settings: "设置…",
            updates: "检查更新…",
            about: "关于 Remedy",
            quit: "退出 Remedy",
            tooltip: "Remedy — 左键单击显示 · 右键单击打开菜单",
        },
        _ => EN,
    }
}

/// `stored` is the ui_language setting. When it is auto (or empty), use the OS locale.
pub fn labels(stored: &str, os_locale: &str) -> TrayCopy {
    let id = if is_auto(stored) {
        locale_id(os_locale)
    } else {
        locale_id(stored)
    };
    copy_for(id)
}

/// Windows user locale, or `LANG` elsewhere. Empty when unknown.
pub fn os_locale_name() -> String {
    #[cfg(windows)]
    {
        let mut buf = [0u16; 85];
        let n = unsafe { GetUserDefaultLocaleName(buf.as_mut_ptr(), buf.len() as i32) };
        if n > 1 {
            return String::from_utf16_lossy(&buf[..(n as usize) - 1]);
        }
        return String::new();
    }
    #[cfg(not(windows))]
    {
        std::env::var("LANG")
            .unwrap_or_default()
            .split('.')
            .next()
            .unwrap_or("")
            .replace('_', "-")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn english_strings_stay_exact() {
        let c = labels("auto", "en-US");
        assert_eq!(c.show, "Show Remedy");
        assert_eq!(c.settings, "Settings...");
        assert_eq!(c.updates, "Check for updates...");
        assert_eq!(c.about, "About Remedy");
        assert_eq!(c.quit, "Quit Remedy");
        assert_eq!(
            c.tooltip,
            "Remedy — left-click to show · right-click for menu"
        );
    }

    #[test]
    fn stored_japanese_beats_english_os() {
        let c = labels("ja", "en-US");
        assert_eq!(c.show, "Remedy を表示");
        assert_eq!(c.quit, "Remedy を終了");
    }

    #[test]
    fn auto_uses_os_german() {
        let c = labels("auto", "de-DE");
        assert_eq!(c.show, "Remedy anzeigen");
        assert_eq!(c.settings, "Einstellungen…");
    }

    #[test]
    fn traditional_chinese_stays_english() {
        assert_eq!(labels("zh-Hant", "ja").show, "Show Remedy");
        assert_eq!(labels("zh-TW", "zh-CN").show, "Show Remedy");
        assert_eq!(labels("auto", "zh-HK").show, "Show Remedy");
    }

    #[test]
    fn portuguese_brazil_and_simplified_chinese() {
        assert_eq!(labels("pt-BR", "en-US").settings, "Configurações…");
        assert_eq!(labels("zh-CN", "en-US").quit, "退出 Remedy");
    }

    #[test]
    fn unknown_language_stays_english() {
        assert_eq!(labels("sv", "en-US").quit, "Quit Remedy");
        assert_eq!(labels("", "fr-FR").show, "Afficher Remedy");
    }
}
