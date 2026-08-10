"""Sonda de correo: comprueba las credenciales SMTP con un mensaje minimo.

Aislada del analisis a proposito: si falla, sabes que el problema es el correo
y no el bot. No lee nada de Biwenger, no usa el token.

Uso:
    py probe_email.py --dry-run     # muestra la config enmascarada, no envia
    py probe_email.py               # envia un mensaje de prueba
"""

import argparse
import sys
from datetime import datetime

import config
from mailer import MailError, build_message, send

TEXT = """\
Prueba de conexion del bot de Biwenger.

Si lees esto, el envio por SMTP funciona: remitente, contrasena de aplicacion
y destinatario son correctos.

Este mensaje es solo informativo. El bot esta en modo propuesta: nunca ejecuta
acciones en Biwenger.

Generado: {stamp}
"""

HTML = """\
<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:16px;background:#eeeeee">
<div style="max-width:560px;margin:0 auto;padding:20px;background:#ffffff;
 font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a">
  <h1 style="margin:0 0 8px 0;font-size:18px">Prueba de conexion</h1>
  <p style="font-size:14px;line-height:1.5">
    Si lees esto, el envio por SMTP funciona: remitente, contrasena de
    aplicacion y destinatario son correctos. Tambien confirma que el correo
    multiparte (texto + HTML) se renderiza bien en tu cliente.
  </p>
  <div style="margin:14px 0;padding:10px 12px;background:#d6f0e0;color:#0f3d24;
   border-radius:4px;font-size:13px;font-weight:700">
    MODO PROPUESTA: el bot solo lee. Este correo no ejecuta nada.
  </div>
  <p style="font-size:11px;color:#5b5b5b;margin:0">Generado: {stamp}</p>
</div></body></html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sonda de correo (1 envio)")
    parser.add_argument("--dry-run", action="store_true",
                        help="mostrar la configuracion y salir sin enviar")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        mail = config.load_mail()
    except config.ConfigError as exc:
        print(f"ERROR de configuracion del correo:\n{exc}", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 70)
    print("SONDA DE CORREO")
    print("=" * 70)
    print(f"  servidor: {mail['host']}:{mail['port']}"
          + ("  (SSL directo)" if mail["port"] == 465 else "  (STARTTLS)"))
    print(f"  de: {mail['sender']}")
    print(f"  para: {', '.join(mail['recipients'])}")
    print(f"  contrasena: {config.mask(mail['password'], keep=2)}")
    print(f"  longitud de la contrasena: {len(mail['password'])} "
          "(una de aplicacion de Gmail tiene 16)")

    if len(mail["password"]) != 16:
        print("  AVISO: no tiene 16 caracteres. Si es tu contrasena normal de "
              "Google, Gmail la rechazara: hace falta una de aplicacion.")

    if args.dry_run:
        print("\n--dry-run: no se ha enviado nada.")
        return 0

    message = build_message(
        subject=f"[Biwenger propuesta] prueba de conexion {stamp}",
        text_body=TEXT.format(stamp=stamp),
        html_body=HTML.format(stamp=stamp),
        sender=mail["sender"],
        recipients=mail["recipients"],
    )

    try:
        send(message, mail["sender"], mail["password"], mail["host"], mail["port"])
    except MailError as exc:
        print(f"\nERROR:\n{exc}", file=sys.stderr)
        return 7

    print(f"\nenviado a {', '.join(mail['recipients'])}. Revisa la bandeja "
          "(y la carpeta de spam la primera vez).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
