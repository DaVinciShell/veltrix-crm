"""Notificación externa opcional; desactivada salvo configuración explícita."""
import json
import os
import re
import urllib.request
from urllib.parse import quote, urlparse


def main():
    if os.environ.get("VELTRIX_NTFY_ENABLED") != "1":
        print("[VELTRIX] Notificación desactivada; no se realizó ninguna solicitud.")
        return 0

    topic = os.environ.get("VELTRIX_NTFY_TOPIC", "").strip()
    server = os.environ.get("VELTRIX_NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
    parsed = urlparse(server)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
        print("[ERROR] VELTRIX_NTFY_TOPIC falta o no tiene un formato permitido.")
        return 2
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        print("[ERROR] VELTRIX_NTFY_SERVER debe ser una URL HTTPS válida.")
        return 2

    payload = {
        "message": "VELTRIX: ejecución programada.",
        "title": "VELTRIX",
        "priority": 3,
        "tags": ["briefcase"]
    }
    headers = {"Content-Type": "application/json", "User-Agent": "Veltrix-CRM/1.0"}
    token = os.environ.get("VELTRIX_NTFY_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token

    req = urllib.request.Request(
        server + "/" + quote(topic, safe=""),
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if 200 <= response.getcode() < 300:
                print("[OK] Notificación enviada.")
                return 0
            print("[ERROR] El servicio rechazó la notificación.")
            return 1
    except Exception as exc:
        print("[ERROR] Falló el envío (" + type(exc).__name__ + ").")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
