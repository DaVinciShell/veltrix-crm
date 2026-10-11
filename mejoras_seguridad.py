"""Seguridad: inicio de sesión, sesiones, copias de seguridad verificadas y restauración.

- Contraseñas con hash (werkzeug: scrypt/pbkdf2 con sal). Nunca en texto plano.
- Sesión en cookie firmada (HttpOnly, SameSite=Strict). Clave de firma en data/secret.key.
- VELTRIX_AUTH=0 solo para pruebas locales; la app se niega a abrirse en red con la autenticación apagada.
"""
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import jsonify, redirect, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash

MIN_CLAVE = 10
_fallos = {}          # (ip, usuario) -> [timestamps]
_lock = threading.Lock()

LOGIN_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VELTRIX CRM · Acceso</title><style>
body{font:15px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif;background:#0f1b2d;color:#16202e;display:grid;place-items:center;min-height:100vh;margin:0}
.b{background:#fff;border-radius:12px;padding:28px;width:min(380px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.4)}
h1{font-size:20px;margin:0 0 4px}p{color:#6b778a;margin:0 0 14px;font-size:13px}label{display:block;font-size:12px;color:#6b778a;margin:10px 0 3px}
input{width:100%;padding:9px 11px;border:1px solid #ccd4e0;border-radius:8px;font:inherit;box-sizing:border-box}
button{margin-top:16px;width:100%;padding:10px;border:0;border-radius:8px;background:#0f1b2d;color:#fff;font:inherit;cursor:pointer}
#e{color:#c2392f;font-size:13px;margin-top:10px;min-height:18px}.logo{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:8px;background:#b8893a;color:#fff;font-weight:800;margin-bottom:8px}
</style></head><body><form class="b" id="f"><div class="logo">V</div><h1 id="t">VELTRIX CRM</h1><p id="s"></p>
<label>Usuario</label><input id="u" autocomplete="username" required><label>Contraseña</label><input id="p" type="password" autocomplete="current-password" required>
<div id="r" style="display:none"><label>Repita la contraseña</label><input id="p2" type="password" autocomplete="new-password"></div>
<button id="g">Entrar</button><div id="e"></div></form><script>
let setup=false;const $=i=>document.getElementById(i);
fetch('/api/auth/estado').then(r=>r.json()).then(j=>{if(j.usuario)location='/';setup=j.setup;
if(setup){$('t').textContent='Crear administrador';$('s').textContent='Primer uso: defina usuario y contraseña (mínimo 10 caracteres).';$('r').style.display='block';$('g').textContent='Crear y entrar'}
else $('s').textContent='Ingrese con su usuario.'});
$('f').onsubmit=async ev=>{ev.preventDefault();$('e').textContent='';
if(setup&&$('p').value!==$('p2').value){$('e').textContent='Las contraseñas no coinciden.';return}
const r=await fetch(setup?'/api/auth/setup':'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({usuario:$('u').value,clave:$('p').value})});
const j=await r.json().catch(()=>({}));if(r.ok)location='/';else $('e').textContent=j.error||'Error';};
</script></body></html>"""


def auth_on():
    return os.environ.get("VELTRIX_AUTH", "1") != "0"


# ------------------------------------------------------------------ copias de seguridad
def dir_respaldos(data_dir):
    return os.path.join(data_dir, "respaldos")


def verificar_db(path):
    """Devuelve (integridad_ok, n_empresas). Abre en solo lectura."""
    c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        n = c.execute("SELECT COUNT(*) FROM empresas").fetchone()[0]
        return ok, n
    finally:
        c.close()


def hacer_respaldo(A, etiqueta="auto"):
    """Copia consistente (API de backup de SQLite) y la verifica. Lanza error si no pasa."""
    d = dir_respaldos(A.DATA_DIR)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, f"crm_{etiqueta}_{datetime.now(A.TZ):%Y%m%d_%H%M%S}.db")
    src = sqlite3.connect(A.DB_PATH, timeout=30)
    out = sqlite3.connect(dst)
    try:
        src.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")   # la copia queda en un solo archivo, sin -wal/-shm
    finally:
        out.close()
        src.close()
    ok, n = verificar_db(dst)
    if not ok:
        os.remove(dst)
        raise RuntimeError("La copia no pasó la verificación de integridad.")
    return {"archivo": os.path.basename(dst), "empresas": n, "bytes": os.path.getsize(dst)}


def listar_respaldos(A):
    d = dir_respaldos(A.DATA_DIR)
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if re.fullmatch(r"crm_[a-z_]+_\d{8}_\d{6}\.db", f):
            p = os.path.join(d, f)
            out.append({"archivo": f, "bytes": os.path.getsize(p),
                        "fecha": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")})
    return out


def podar(A, conservar=14):
    """Deja las últimas N copias automáticas. Las manuales y pre_migracion no se borran."""
    d = dir_respaldos(A.DATA_DIR)
    autos = [f for f in sorted(os.listdir(d), reverse=True) if f.startswith("crm_auto_")] if os.path.isdir(d) else []
    for f in autos[conservar:]:
        os.remove(os.path.join(d, f))


def respaldo_si_toca(A, horas=24):
    r = listar_respaldos(A)
    autos = [x for x in r if x["archivo"].startswith("crm_auto_")]
    if autos:
        ult = datetime.strptime(autos[0]["archivo"][-18:-3], "%Y%m%d_%H%M%S")
        if datetime.now() - ult < timedelta(hours=horas):
            return None
    res = hacer_respaldo(A, "auto")
    podar(A)
    return res


def restaurar(db_path, backup_path):
    """Restaura una copia. EJECUTAR CON EL SERVIDOR DETENIDO. Guarda antes la base actual."""
    ok, n = verificar_db(backup_path)
    if not ok or n == 0:
        raise RuntimeError("La copia está dañada o vacía; no se restaura.")
    carpeta = os.path.join(os.path.dirname(db_path), "respaldos")
    os.makedirs(carpeta, exist_ok=True)
    seguridad = None
    if os.path.exists(db_path):
        seguridad = os.path.join(carpeta, f"crm_antesrestaurar_{datetime.now():%Y%m%d_%H%M%S}.db")
        s = sqlite3.connect(db_path)
        o = sqlite3.connect(seguridad)
        s.backup(o)
        o.close()
        s.close()
    for ext in ("-wal", "-shm"):
        if os.path.exists(db_path + ext):
            os.remove(db_path + ext)
    shutil.copy2(backup_path, db_path)
    ok2, n2 = verificar_db(db_path)
    if not ok2:
        raise RuntimeError("La base restaurada no pasó la verificación.")
    return {"empresas": n2, "copia_de_seguridad_previa": seguridad}


# ------------------------------------------------------------------ autenticación
def migrate(db):
    db.executescript("""CREATE TABLE IF NOT EXISTS usuarios(
        id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT UNIQUE NOT NULL, hash TEXT NOT NULL, creado TEXT);""")


def _clave_secreta(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    p = os.path.join(data_dir, "secret.key")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return open(p).read().strip()


def _bloqueado(ip, usuario):
    ahora = time.time()
    with _lock:
        v = [t for t in _fallos.get((ip, usuario), []) if ahora - t < 300]
        _fallos[(ip, usuario)] = v
        return len(v) >= 5


def _falla(ip, usuario):
    with _lock:
        _fallos.setdefault((ip, usuario), []).append(time.time())


def register(app, A):
    app.secret_key = _clave_secreta(A.DATA_DIR)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))

    ABIERTAS = ("/login", "/api/auth/")

    @app.before_request
    def _guardia():
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            o = request.headers.get("Origin")
            if o and urlparse(o).netloc != request.host:
                return jsonify({"error": "Origen no permitido."}), 403
        if not auth_on():
            return None
        p = request.path
        if p == "/login" or p.startswith("/api/auth/"):
            return None
        if session.get("u"):
            return None
        if p.startswith("/api/"):
            return jsonify({"error": "Sesión requerida.", "login": True}), 401
        return redirect("/login")

    @app.after_request
    def _cabeceras(r):
        r.headers.setdefault("X-Content-Type-Options", "nosniff")
        r.headers.setdefault("X-Frame-Options", "DENY")
        r.headers.setdefault("Cache-Control", "no-store")
        return r

    @app.get("/login")
    def login_page():
        return LOGIN_HTML

    @app.get("/api/auth/estado")
    def auth_estado():
        n = A.one("SELECT COUNT(*) n FROM usuarios")["n"]
        return jsonify({"auth": auth_on(), "setup": n == 0, "usuario": session.get("u")})

    def _valida(d):
        u = (d.get("usuario") or "").strip()
        c = d.get("clave") or ""
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,40}", u):
            raise A.ApiError("Usuario: 3 a 40 caracteres (letras, números, . _ - @).")
        if len(c) < MIN_CLAVE:
            raise A.ApiError(f"La contraseña debe tener al menos {MIN_CLAVE} caracteres.")
        return u, c

    @app.post("/api/auth/setup")
    def auth_setup():
        if A.one("SELECT COUNT(*) n FROM usuarios")["n"] > 0:
            raise A.ApiError("Ya existe un administrador.", 403)
        if request.remote_addr not in ("127.0.0.1", "::1"):
            raise A.ApiError("La configuración inicial solo puede hacerse desde este mismo equipo.", 403)
        u, c = _valida(A.body())
        A.get_db().execute("INSERT INTO usuarios(usuario,hash,creado) VALUES(?,?,?)", (u, generate_password_hash(c), A.ahora()))
        A.get_db().commit()
        session.clear()
        session.permanent = True
        session["u"] = u
        return jsonify({"ok": True})

    @app.post("/api/auth/login")
    def auth_login():
        d = A.body()
        u = (d.get("usuario") or "").strip()
        ip = request.remote_addr
        if _bloqueado(ip, u):
            raise A.ApiError("Demasiados intentos. Espere 5 minutos.", 429)
        r = A.one("SELECT * FROM usuarios WHERE usuario=?", (u,))
        if not r or not check_password_hash(r["hash"], d.get("clave") or ""):
            _falla(ip, u)
            raise A.ApiError("Usuario o contraseña incorrectos.", 401)
        session.clear()
        session.permanent = True
        session["u"] = u
        return jsonify({"ok": True})

    @app.post("/api/auth/logout")
    def auth_logout():
        session.clear()
        return jsonify({"ok": True})

    @app.post("/api/auth/cambiar")
    def auth_cambiar():
        d = A.body()
        r = A.one("SELECT * FROM usuarios WHERE usuario=?", (session.get("u"),))
        if not r or not check_password_hash(r["hash"], d.get("actual") or ""):
            raise A.ApiError("La contraseña actual no es correcta.", 401)
        if len(d.get("nueva") or "") < MIN_CLAVE:
            raise A.ApiError(f"La nueva contraseña debe tener al menos {MIN_CLAVE} caracteres.")
        A.get_db().execute("UPDATE usuarios SET hash=? WHERE id=?", (generate_password_hash(d["nueva"]), r["id"]))
        A.get_db().commit()
        return jsonify({"ok": True})

    # ---- respaldos
    @app.get("/api/respaldos")
    def api_respaldos():
        return jsonify(listar_respaldos(A))

    @app.post("/api/respaldos")
    def api_crear_respaldo():
        return jsonify(hacer_respaldo(A, "manual")), 201

    @app.get("/api/respaldos/<nombre>")
    def api_bajar_respaldo(nombre):
        if not re.fullmatch(r"crm_[a-z_]+_\d{8}_\d{6}\.db", nombre):
            raise A.ApiError("Nombre no válido.", 400)
        p = os.path.join(dir_respaldos(A.DATA_DIR), nombre)
        if not os.path.exists(p):
            raise A.ApiError("No existe.", 404)
        return send_file(p, as_attachment=True, download_name=nombre)
