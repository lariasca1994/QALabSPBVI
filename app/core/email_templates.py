"""Plantilla de correo transaccional de QALabSPBVI.

Reglas (CLAUDE.md, "Experiencia visual"): CSS inline, una sola columna adaptable,
sin JavaScript, hojas externas, fuentes remotas ni imágenes; siempre con alternativa
en texto plano. Todo valor dinámico se escapa. Nunca incluir credenciales, tokens de
sesión ni datos sensibles de pagos; la única excepción es el código MFA de un uso.

Los colores siguen la paleta de la interfaz (web/src/styles.css), inspirada en Banrep
como referencia; no se afirma que sean los colores oficiales de la marca.
"""

from dataclasses import dataclass, field
from html import escape

# Paleta clara de la interfaz. Los clientes con tema oscuro suelen invertirla;
# el bloque <style> opcional mejora el contraste donde se admite.
_PAGE = "#f3f6f8"
_SURFACE = "#ffffff"
_TEXT = "#183448"
_TEXT_STRONG = "#102b3f"
_MUTED = "#71818d"
_LINE = "#e4eaed"
_BLUE = "#17658a"
_BLUE_SOFT = "#eaf3f7"
_GOLD = "#b58a3b"
_FONT = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
_MONO = "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace"


@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class EmailMessage:
    """Contenido neutral de un correo; render_email lo convierte en texto y HTML."""

    subject: str
    eyebrow: str
    heading: str
    greeting: str
    paragraphs: list[str] = field(default_factory=list)
    details: list[tuple[str, str]] = field(default_factory=list)
    code: str | None = None
    code_caption: str | None = None
    notice: str | None = None


def _text_version(message: EmailMessage) -> str:
    lines = [message.greeting, ""]
    lines.extend(f"{paragraph}\n" for paragraph in message.paragraphs)
    if message.code:
        lines.append(f"Código: {message.code}")
        if message.code_caption:
            lines.append(message.code_caption)
        lines.append("")
    for label, value in message.details:
        lines.append(f"{label}: {value}")
    if message.details:
        lines.append("")
    if message.notice:
        lines.extend([message.notice, ""])
    lines.append(
        "QALabSPBVI · Laboratorio simulado de pagos inmediatos. "
        "No conecta con infraestructura real de pagos."
    )
    return "\n".join(lines).strip() + "\n"


def _html_version(message: EmailMessage) -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:{_TEXT};">'
        f"{escape(paragraph)}</p>"
        for paragraph in message.paragraphs
    )
    code_block = ""
    if message.code:
        caption = (
            f'<p style="margin:10px 0 0;font-size:13px;line-height:1.5;color:{_MUTED};">'
            f"{escape(message.code_caption)}</p>"
            if message.code_caption
            else ""
        )
        code_block = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin:4px 0 20px;border-collapse:separate;">'
            f'<tr><td align="center" style="background:{_BLUE_SOFT};border:1px solid {_LINE};'
            'border-radius:10px;padding:20px 16px;">'
            f'<div class="qa-code" style="font-family:{_MONO};font-size:30px;font-weight:700;'
            f'letter-spacing:8px;color:{_TEXT_STRONG};">{escape(message.code)}</div>'
            f"{caption}</td></tr></table>"
        )
    details = ""
    if message.details:
        rows = "".join(
            "<tr>"
            f'<td style="padding:9px 12px 9px 0;border-top:1px solid {_LINE};font-size:13px;'
            f'color:{_MUTED};white-space:nowrap;vertical-align:top;">{escape(label)}</td>'
            f'<td style="padding:9px 0;border-top:1px solid {_LINE};font-size:14px;'
            f'color:{_TEXT_STRONG};word-break:break-word;">{escape(value)}</td>'
            "</tr>"
            for label, value in message.details
        )
        details = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin:0 0 20px;border-collapse:collapse;">{rows}</table>'
        )
    notice = (
        f'<p style="margin:0 0 4px;padding:12px 14px;border-left:3px solid {_GOLD};'
        f'background:{_PAGE};font-size:13px;line-height:1.5;color:{_TEXT};">'
        f"{escape(message.notice)}</p>"
        if message.notice
        else ""
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{escape(message.subject)}</title>
<style>
@media (prefers-color-scheme: dark) {{
  .qa-page {{ background:#0f1d27 !important; }}
  .qa-card {{ background:#172934 !important; border-color:#2b414d !important; }}
  .qa-card p, .qa-card td, .qa-card h1 {{ color:#dce7ed !important; }}
  .qa-code {{ color:#f0f5f7 !important; }}
}}
</style>
</head>
<body style="margin:0;padding:0;background:{_PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(message.heading)}</div>
<table role="presentation" class="qa-page" width="100%" cellpadding="0" cellspacing="0" style="background:{_PAGE};">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;font-family:{_FONT};">
<tr><td style="padding:0 4px 14px;font-size:14px;font-weight:700;color:{_TEXT_STRONG};">
<span style="display:inline-block;width:10px;height:10px;margin-right:8px;border-radius:3px;background:{_BLUE};"></span>QALabSPBVI
</td></tr>
<tr><td class="qa-card" style="background:{_SURFACE};border:1px solid {_LINE};border-top:3px solid {_BLUE};border-radius:12px;padding:28px 24px;">
<p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;color:{_BLUE};">{escape(message.eyebrow)}</p>
<h1 style="margin:0 0 18px;font-size:22px;line-height:1.3;color:{_TEXT_STRONG};">{escape(message.heading)}</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:{_TEXT};">{escape(message.greeting)}</p>
{paragraphs}{code_block}{details}{notice}
</td></tr>
<tr><td style="padding:16px 8px 0;font-size:12px;line-height:1.5;color:{_MUTED};text-align:center;">
Laboratorio simulado de pagos inmediatos. No conecta con infraestructura real de pagos.<br>
Este es un mensaje automático; no respondas a este correo.
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def render_email(message: EmailMessage) -> EmailContent:
    return EmailContent(
        subject=message.subject,
        text=_text_version(message),
        html=_html_version(message),
    )
