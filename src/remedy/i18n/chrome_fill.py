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
