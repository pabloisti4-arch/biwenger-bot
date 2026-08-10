"""Carga de configuracion desde variables de entorno.

Nada de credenciales en el codigo: todo viene del entorno.
"""

import os

# Variables obligatorias para la Fase 1 (token capturado a mano).
REQUIRED = ("BIWENGER_TOKEN", "BIWENGER_LEAGUE", "BIWENGER_USER")

# Valores por defecto de las opcionales.
DEFAULTS = {
    "BIWENGER_COMPETITION": "la-liga",
    "BIWENGER_LANG": "es",
    "BIWENGER_SCORE": "1",
    # Biwenger incrementa este numero con cada build del frontend y responde
    # 400 "Old version" a las peticiones con un valor viejo. Capturado de
    # DevTools el 10-08-2026; cuando vuelva a fallar, actualiza la variable
    # de entorno BIWENGER_VERSION en vez de tocar el codigo.
    "BIWENGER_VERSION": "631",
}


class ConfigError(RuntimeError):
    """La configuracion del entorno esta incompleta o mal formada."""


def _optionals():
    """Campos comunes a los dos modos de autenticacion."""
    return {
        "competition": os.environ.get("BIWENGER_COMPETITION", "").strip()
        or DEFAULTS["BIWENGER_COMPETITION"],
        "lang": os.environ.get("BIWENGER_LANG", "").strip() or DEFAULTS["BIWENGER_LANG"],
        "score": os.environ.get("BIWENGER_SCORE", "").strip() or DEFAULTS["BIWENGER_SCORE"],
        "version": os.environ.get("BIWENGER_VERSION", "").strip()
        or DEFAULTS["BIWENGER_VERSION"],
    }


def score_was_explicit():
    """True si el usuario fijo BIWENGER_SCORE a mano.

    Sirve para no pisar su eleccion cuando el login deduce el scoreID de la liga.
    """
    return bool(os.environ.get("BIWENGER_SCORE", "").strip())


def load_auth():
    """Config de autenticacion en cualquiera de los dos modos.

    modo 'token'     BIWENGER_TOKEN capturado a mano (Fases 1-3, local).
    modo 'password'  BIWENGER_EMAIL + BIWENGER_PASSWORD, para GitHub Actions:
                     el token se pide fresco en cada ejecucion y no caduca
                     entre runs. league/user se deducen de /account si no se dan.
    """
    token = os.environ.get("BIWENGER_TOKEN", "").strip()
    email = os.environ.get("BIWENGER_EMAIL", "").strip()
    password = os.environ.get("BIWENGER_PASSWORD", "")

    if token:
        return {"mode": "token", **load()}

    if email and password.strip():
        return {
            "mode": "password",
            "email": email,
            "password": password,
            # Opcionales: si faltan, se deducen de /account tras el login.
            "league": os.environ.get("BIWENGER_LEAGUE", "").strip(),
            "user": os.environ.get("BIWENGER_USER", "").strip(),
            "token": "",
            **_optionals(),
        }

    raise ConfigError(
        "No hay credenciales. Define UNA de estas dos opciones:\n"
        "  A) BIWENGER_TOKEN + BIWENGER_LEAGUE + BIWENGER_USER  (token de DevTools)\n"
        "  B) BIWENGER_EMAIL + BIWENGER_PASSWORD                (login programatico)\n"
        "La opcion B es la de GitHub Actions: el token se renueva en cada ejecucion."
    )


def load():
    missing = [k for k in REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        raise ConfigError(
            "Faltan variables de entorno: "
            + ", ".join(missing)
            + "\nDefinelas en PowerShell (ver README.md) y abre una consola nueva."
        )

    token = os.environ["BIWENGER_TOKEN"].strip()
    # Por comodidad: si pegaste el header completo, quitamos el prefijo.
    if token.lower().startswith("bearer "):
        token = token[len("bearer ") :].strip()

    return {
        "token": token,
        "league": os.environ["BIWENGER_LEAGUE"].strip(),
        "user": os.environ["BIWENGER_USER"].strip(),
        **_optionals(),
    }


MAIL_REQUIRED = (
    "BIWENGER_MAIL_FROM",
    "BIWENGER_MAIL_PASSWORD",
    "BIWENGER_MAIL_TO",
)

MAIL_DEFAULTS = {
    "BIWENGER_MAIL_HOST": "smtp.gmail.com",
    "BIWENGER_MAIL_PORT": "465",
}


def load_mail():
    """Config del correo, aparte de la del analisis.

    Deliberadamente separada: el analisis debe funcionar sin tener el correo
    configurado. Solo se pide cuando se va a enviar.
    """
    missing = [k for k in MAIL_REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        raise ConfigError(
            "Faltan variables de entorno del correo: "
            + ", ".join(missing)
            + "\nDefinelas en PowerShell (ver README.md) y abre una consola nueva."
        )

    # Gmail muestra la contraseña de aplicacion como 4 grupos de 4 separados por
    # espacios. Se aceptan las dos formas: aqui se quitan los espacios.
    password = "".join(os.environ["BIWENGER_MAIL_PASSWORD"].split())

    raw_to = os.environ["BIWENGER_MAIL_TO"]
    recipients = [r.strip() for r in raw_to.replace(";", ",").split(",") if r.strip()]
    if not recipients:
        raise ConfigError("BIWENGER_MAIL_TO no contiene ningun destinatario valido")

    port_raw = os.environ.get("BIWENGER_MAIL_PORT", "").strip() or MAIL_DEFAULTS[
        "BIWENGER_MAIL_PORT"
    ]
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ConfigError(f"BIWENGER_MAIL_PORT no es un numero: {port_raw!r}") from exc

    return {
        "sender": os.environ["BIWENGER_MAIL_FROM"].strip(),
        "password": password,
        "recipients": recipients,
        "host": os.environ.get("BIWENGER_MAIL_HOST", "").strip()
        or MAIL_DEFAULTS["BIWENGER_MAIL_HOST"],
        "port": port,
    }


def mask(secret, keep=6):
    """Representacion segura de un secreto, para logs."""
    if not secret:
        return "<vacio>"
    if len(secret) <= keep * 2:
        return "*" * len(secret)
    return f"{secret[:keep]}...{secret[-keep:]} ({len(secret)} chars)"
