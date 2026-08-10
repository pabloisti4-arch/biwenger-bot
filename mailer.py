"""Envio del reporte por SMTP (Gmail con contrasena de aplicacion).

El correo es SOLO informativo: transporta el reporte y nada mas. No lleva
enlaces de accion, ni formularios, ni nada que ejecute algo en Biwenger.

Puertos soportados:
  465  SMTP sobre SSL directo (por defecto, el mas simple)
  587  SMTP con STARTTLS
En ambos casos con verificacion de certificado por defecto.
"""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

SUBJECT_PREFIX = "[Biwenger propuesta]"


class MailError(RuntimeError):
    pass


def build_message(subject, text_body, html_body, sender, recipients,
                  sender_name="Biwenger bot"):
    """Multipart alternative: texto plano + HTML.

    El texto plano no es un adorno: es el fallback real para clientes que no
    renderizan HTML, y va con el contenido completo, no un 'mira el HTML'.
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, sender))
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="biwenger-bot.local")
    # Pista para clientes y filtros: esto es un informe automatico.
    message["Auto-Submitted"] = "auto-generated"

    message.set_content(text_body, subtype="plain", charset="utf-8")
    if html_body:
        message.add_alternative(html_body, subtype="html", charset="utf-8")
    return message


def send(message, sender, password, host, port, timeout=30):
    """Envia el mensaje. Traduce los fallos tipicos a algo accionable."""
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as smtp:
                smtp.login(sender, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(sender, password)
                smtp.send_message(message)

    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "Gmail rechazo las credenciales.\n"
            "  - Tiene que ser una CONTRASENA DE APLICACION, no la de tu cuenta.\n"
            "  - Requiere verificacion en dos pasos activada en la cuenta.\n"
            "  - Comprueba que BIWENGER_MAIL_FROM es la misma cuenta que la genero.\n"
            f"  respuesta del servidor: {exc.smtp_code} {exc.smtp_error!r}"
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(
            f"Destinatario rechazado: {exc.recipients}. Revisa BIWENGER_MAIL_TO."
        ) from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailError(
            f"Remitente rechazado ({exc.sender}). Revisa BIWENGER_MAIL_FROM."
        ) from exc
    except ssl.SSLError as exc:
        raise MailError(
            f"Fallo TLS con {host}:{port}: {exc}. Si usas el puerto 587 asegurate "
            "de que BIWENGER_MAIL_PORT vale 587 (STARTTLS)."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(
            f"No se pudo enviar por {host}:{port}: {exc}\n"
            "Si el error es de conexion, puede ser el firewall o la red "
            "bloqueando el puerto SMTP."
        ) from exc


# Etiquetas legibles para el asunto. `clausulazo_impacto` va a None a proposito:
# es el MISMO conjunto de candidatos que `clausulazo` en otro orden, y contarlo
# aparte inflaria el asunto con oportunidades que no existen.
# `especulacion` se omite para que el asunto no pase de ~70 caracteres, que es
# donde lo cortan la mayoria de clientes; en el cuerpo sigue entera.
SUBJECT_LABELS = {
    "chollo": "compras",
    "venta": "ventas",
    "especulacion": None,
    "clausulazo": "clausulazos",
    "clausulazo_impacto": None,
}


def subject_for(report):
    """Asunto informativo: fecha y lo que trae, sin prometer acciones."""
    counts = []
    for section in report.sections:
        label = SUBJECT_LABELS.get(section.key, section.key)
        if label and section.opportunities:
            counts.append(f"{len(section.opportunities)} {label}")
    resumen = ", ".join(counts) if counts else "sin oportunidades"
    fecha = report.generated_at.strftime("%Y-%m-%d")
    return f"{SUBJECT_PREFIX} {fecha} · {resumen}"
