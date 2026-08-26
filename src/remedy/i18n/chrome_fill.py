"""Missing chrome keys so every chrome=True language covers English."""

from __future__ import annotations

_BRAND = {
    "bar.grove": "Grove",
    "bar.studio": "Studio",
    "bar.webui": "WebUI",
}

FILL: dict[str, dict[str, str]] = {}


def _put(code: str, **rows: str) -> None:
    FILL[code] = {**_BRAND, **rows}


_put(
    "es",
    **{
        "sidebar.newSession": "+ Nueva sesión",
        "sidebar.import": "Importar",
        "sidebar.export": "Exportar",
        "sidebar.search": "Buscar sesiones, proyectos…",
        "sidebar.all": "Todas",
        "sidebar.pin": "★ Fijar",
        "sidebar.archive": "Archivo",
        "bar.plan": "Plan",
        "bar.build": "Construir",
        "bar.privacy": "Privacidad",
        "bar.privacyOn": "Privacidad · sí",
        "approval.required": "Hace falta tu visto bueno",
        "approval.payment": "Paso de pago — te necesita",
        "approval.paymentNote": "Se pregunta siempre — ningún modo salta un pago o un secreto.",
        "approval.details": "Detalles",
        "approval.working": "Trabajando…",
        "approval.yes": "Sí, adelante",
        "approval.once": "Aprobar esta vez",
        "approval.notNow": "Ahora no",
        "approval.deny": "Denegar",
        "approval.approved": "Aprobado",
        "approval.denied": "Denegado",
        "approval.region": "Herramientas en espera",
        "userName.title": "¿Cómo te llama Remedy?",
        "userName.hint": "En el chat y la memoria — cámbialo cuando quieras en Ajustes.",
        "userName.placeholder": "Tu nombre",
        "userName.later": "Luego",
        "userName.save": "Guardar",
        "settings.yourNameHint": "Se guarda en tu perfil para que Remedy te hable con naturalidad.",
        "settings.partnerNameHint": "Llama a tu pareja como quieras — el valor por defecto es Remedy.",
        "settings.partnerGender": "Género de la pareja",
        "settings.female": "Mujer",
        "settings.male": "Hombre",
        "settings.neutral": "Ninguno / IA",
        "settings.genderHint": "Solo presentación — no es médico. Por defecto mujer; cámbialo cuando quieras.",
        "grove.switchStudio": "Grove ✦ · pasar a Studio",
        "grove.switchStudioShort": "pasar a Studio",
        "grove.happeningNow": "Está pasando ahora",
        "grove.storyline": "Historia",
        "grove.alongside": "Juntas",
        "grove.listening": "Escuchando…",
        "grove.helloLate": "Despierta a deshoras",
        "grove.helloMorning": "Buenos días",
        "grove.helloAfternoon": "Buenas tardes",
        "grove.helloEvening": "Buenas noches",
        "bar.groveTitle": "Grove — tu casa con tu pareja (metas, acciones, historia)",
        "bar.studioTitle": "Studio — el taller (archivos, terminal, navegador)",
    },
)

_put(
    "pt",
    **{
        "bar.groveTitle": "Grove — sua casa com a parceira (metas, ações, história)",
        "bar.studioTitle": "Studio — a bancada (arquivos, terminal, navegador)",
        "bar.advancedTitle": "Interface avançada — clique para Simples",
        "bar.simpleTitle": "Interface simples — clique para Avançado",
        "sidebar.newSession": "+ Nova sessão",
        "sidebar.import": "Importar",
        "sidebar.export": "Exportar",
        "sidebar.search": "Buscar sessões, projetos…",
        "sidebar.all": "Todas",
        "sidebar.pin": "★ Fixar",
        "sidebar.archive": "Arquivo",
        "bar.plan": "Plano",
        "bar.build": "Construir",
        "bar.privacy": "Privacidade",
        "bar.privacyOn": "Privacidade · sim",
        "approval.required": "Precisa da sua aprovação",
        "approval.payment": "Passo de pagamento — precisa de você",
        "approval.paymentNote": "Pergunta sempre — nenhum modo pula pagamento ou segredo.",
        "approval.details": "Detalhes",
        "approval.working": "Trabalhando…",
        "approval.yes": "Sim, pode ir",
        "approval.once": "Aprovar desta vez",
        "approval.notNow": "Agora não",
        "approval.deny": "Recusar",
        "approval.approved": "Aprovado",
        "approval.denied": "Recusado",
        "approval.region": "Ferramentas à espera",
        "userName.title": "Como o Remedy deve te chamar?",
        "userName.hint": "No chat e na memória — mude quando quiser em Configurações.",
        "userName.placeholder": "Seu nome",
        "userName.later": "Depois",
        "userName.save": "Salvar",
        "settings.yourNameHint": "Salvo no seu perfil para o Remedy te chamar com naturalidade.",
        "settings.partnerNameHint": "Chame a parceira como quiser — o padrão é Remedy.",
        "settings.partnerGender": "Gênero da parceira",
        "settings.female": "Mulher",
        "settings.male": "Homem",
        "settings.neutral": "Nenhum / IA",
        "settings.genderHint": "Só apresentação — não é médico. Padrão mulher; mude quando quiser.",
        "grove.switchStudio": "Grove ✦ · ir para Studio",
        "grove.switchStudioShort": "ir para Studio",
        "grove.happeningNow": "Acontecendo agora",
        "grove.storyline": "História",
        "grove.alongside": "Juntas",
        "grove.listening": "Ouvindo…",
        "grove.helloLate": "Acordada tarde",
        "grove.helloMorning": "Bom dia",
        "grove.helloAfternoon": "Boa tarde",
        "grove.helloEvening": "Boa noite",
        "composer.placeholderEditQueue": "Editar mensagem na fila — Enter salva",
        "composer.placeholderHearing": "Entendendo o que você disse…",
        "composer.placeholderListening": "Ouvindo…",
        "menu.diagnostics": "Diagnóstico",
        "menu.memory": "Memória",
        "menu.open": "Abrir menu do Remedy",
        "win.restore": "Restaurar",
    },
)

from remedy.i18n import (
    chrome_fill_more,  # noqa: F401
    chrome_fill_tail,  # noqa: F401
)
from remedy.i18n.chrome_fill_rest import REST

for _code, _rows in REST.items():
    FILL[_code] = {**_BRAND, **FILL.get(_code, {}), **_rows}

# Chat pin next to Plan / Build — every chrome=True overlay must cover EN.
_CHAT_PIN: dict[str, dict[str, str]] = {
    "es": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Solo hablar — Mayús+Tab para Plan",
        "composer.hintChat": "Chat · sin herramientas · Mayús+Tab Plan",
    },
    "pt": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Só conversar — Shift+Tab para Plano",
        "composer.hintChat": "Chat · sem ferramentas · Shift+Tab Plano",
    },
    "fr": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Juste parler — Maj+Tab pour Plan",
        "composer.hintChat": "Chat · pas d’outils · Maj+Tab Plan",
    },
    "de": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Nur reden — Umschalt+Tab für Plan",
        "composer.hintChat": "Chat · keine Tools · Umschalt+Tab Plan",
    },
    "it": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Solo parlare — Maiusc+Tab per Piano",
        "composer.hintChat": "Chat · niente strumenti · Maiusc+Tab Piano",
    },
    "nl": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Gewoon praten — Shift+Tab voor Plan",
        "composer.hintChat": "Chat · geen tools · Shift+Tab Plan",
    },
    "pl": {
        "bar.chat": "Czat",
        "composer.placeholderChat": "Po prostu rozmawiaj — Shift+Tab do Planu",
        "composer.hintChat": "Czat · bez narzędzi · Shift+Tab Plan",
    },
    "ru": {
        "bar.chat": "Чат",
        "composer.placeholderChat": "Просто поговорить — Shift+Tab к плану",
        "composer.hintChat": "Чат · без инструментов · Shift+Tab План",
    },
    "uk": {
        "bar.chat": "Чат",
        "composer.placeholderChat": "Просто поговорити — Shift+Tab до плану",
        "composer.hintChat": "Чат · без інструментів · Shift+Tab План",
    },
    "tr": {
        "bar.chat": "Sohbet",
        "composer.placeholderChat": "Sadece konuş — Shift+Tab Plan",
        "composer.hintChat": "Sohbet · araç yok · Shift+Tab Plan",
    },
    "ar": {
        "bar.chat": "دردشة",
        "composer.placeholderChat": "مجرد حديث — Shift+Tab للخطة",
        "composer.hintChat": "دردشة · بلا أدوات · Shift+Tab خطة",
    },
    "hi": {
        "bar.chat": "चैट",
        "composer.placeholderChat": "सिर्फ़ बात — Shift+Tab योजना",
        "composer.hintChat": "चैट · बिना टूल · Shift+Tab योजना",
    },
    "bn": {
        "bar.chat": "চ্যাট",
        "composer.placeholderChat": "শুধু কথা — Shift+Tab পরিকল্পনা",
        "composer.hintChat": "চ্যাট · টুল নেই · Shift+Tab পরিকল্পনা",
    },
    "ur": {
        "bar.chat": "چیٹ",
        "composer.placeholderChat": "صرف بات — Shift+Tab منصوبہ",
        "composer.hintChat": "چیٹ · بغیر ٹول · Shift+Tab منصوبہ",
    },
    "id": {
        "bar.chat": "Obrolan",
        "composer.placeholderChat": "Hanya ngobrol — Shift+Tab ke Rencana",
        "composer.hintChat": "Obrolan · tanpa alat · Shift+Tab Rencana",
    },
    "ms": {
        "bar.chat": "Sembang",
        "composer.placeholderChat": "Sekadar sembang — Shift+Tab ke Rancangan",
        "composer.hintChat": "Sembang · tiada alat · Shift+Tab Rancangan",
    },
    "vi": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Chỉ nói chuyện — Shift+Tab sang Kế hoạch",
        "composer.hintChat": "Chat · không công cụ · Shift+Tab Kế hoạch",
    },
    "th": {
        "bar.chat": "แชท",
        "composer.placeholderChat": "คุยอย่างเดียว — Shift+Tab ไปแผน",
        "composer.hintChat": "แชท · ไม่ใช้เครื่องมือ · Shift+Tab แผน",
    },
    "ja": {
        "bar.chat": "チャット",
        "composer.placeholderChat": "話すだけ — Shift+Tab で計画",
        "composer.hintChat": "チャット · ツールなし · Shift+Tab 計画",
    },
    "ko": {
        "bar.chat": "채팅",
        "composer.placeholderChat": "그냥 대화 — Shift+Tab 으로 계획",
        "composer.hintChat": "채팅 · 도구 없음 · Shift+Tab 계획",
    },
    "zh-Hans": {
        "bar.chat": "聊天",
        "composer.placeholderChat": "只是聊聊 — Shift+Tab 到计划",
        "composer.hintChat": "聊天 · 不用工具 · Shift+Tab 计划",
    },
    "zh-Hant": {
        "bar.chat": "聊天",
        "composer.placeholderChat": "只是聊聊 — Shift+Tab 到計畫",
        "composer.hintChat": "聊天 · 不用工具 · Shift+Tab 計畫",
    },
    "fil": {
        "bar.chat": "Usapan",
        "composer.placeholderChat": "Usap lang — Shift+Tab papuntang Plano",
        "composer.hintChat": "Usapan · walang tools · Shift+Tab Plano",
    },
    "sw": {
        "bar.chat": "Soga",
        "composer.placeholderChat": "Ongea tu — Shift+Tab kwenda Mpango",
        "composer.hintChat": "Soga · bila zana · Shift+Tab Mpango",
    },
    "he": {
        "bar.chat": "צ׳אט",
        "composer.placeholderChat": "סתם לדבר — Shift+Tab לתוכנית",
        "composer.hintChat": "צ׳אט · בלי כלים · Shift+Tab תוכנית",
    },
    "fa": {
        "bar.chat": "گفتگو",
        "composer.placeholderChat": "فقط حرف بزن — Shift+Tab به برنامه",
        "composer.hintChat": "گفتگو · بدون ابزار · Shift+Tab برنامه",
    },
    "sv": {
        "bar.chat": "Chatt",
        "composer.placeholderChat": "Bara prata — Shift+Tab till Plan",
        "composer.hintChat": "Chatt · inga verktyg · Shift+Tab Plan",
    },
    "da": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Bare snak — Shift+Tab til Plan",
        "composer.hintChat": "Chat · ingen værktøjer · Shift+Tab Plan",
    },
    "no": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Bare prat — Shift+Tab til Plan",
        "composer.hintChat": "Chat · ingen verktøy · Shift+Tab Plan",
    },
    "fi": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Vain juttelu — Shift+Tab suunnitelmaan",
        "composer.hintChat": "Chat · ei työkaluja · Shift+Tab suunnitelma",
    },
    "hu": {
        "bar.chat": "Csevegés",
        "composer.placeholderChat": "Csak beszélgetés — Shift+Tab a Tervhez",
        "composer.hintChat": "Csevegés · nincs eszköz · Shift+Tab Terv",
    },
    "cs": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Jen mluvit — Shift+Tab na Plán",
        "composer.hintChat": "Chat · bez nástrojů · Shift+Tab Plán",
    },
    "ro": {
        "bar.chat": "Chat",
        "composer.placeholderChat": "Doar vorbim — Shift+Tab spre Plan",
        "composer.hintChat": "Chat · fără unelte · Shift+Tab Plan",
    },
    "el": {
        "bar.chat": "Συνομιλία",
        "composer.placeholderChat": "Απλά κουβέντα — Shift+Tab στο Σχέδιο",
        "composer.hintChat": "Συνομιλία · χωρίς εργαλεία · Shift+Tab Σχέδιο",
    },
    "ca": {
        "bar.chat": "Xat",
        "composer.placeholderChat": "Només parlar — Maj+Tab cap a Pla",
        "composer.hintChat": "Xat · sense eines · Maj+Tab Pla",
    },
}
for _code, _rows in _CHAT_PIN.items():
    FILL[_code] = {**FILL.get(_code, {}), **_rows}
