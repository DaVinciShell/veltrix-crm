#!/usr/bin/env python3
"""VELTRIX CRM - CRM minimo funcional (Flask + SQLite).

Ejecutar:  python app.py   ->  http://localhost:5000
Datos:     data/crm.db (SQLite, persistente). Copia de seguridad desde la app.
"""
import csv
import io
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

import mejoras_avisos as MA
import mejoras_base as MB
import mejoras_docs as MD
import mejoras_agenda as MAG
import mejoras_ia as MIA
import mejoras_seguridad as MS
import mejoras_tablero as MT

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("VELTRIX_DATA", os.path.join(BASE, "data"))
DB_PATH = os.path.join(DATA_DIR, "crm.db")
TZ = ZoneInfo("America/Bogota")

ETAPAS = ["Por contactar", "Contactado", "Conversación iniciada", "Calificado",
          "Reunión agendada", "Propuesta enviada", "Negociación", "Ganado", "Perdido"]
ACTIVAS = ETAPAS[:7]
CALIENTES = {"Calificado", "Reunión agendada", "Propuesta enviada", "Negociación"}
TIPOS_INT = ["Llamada", "Mensaje", "Correo", "Reunión"]
METAS_DEF = {"contactos_nuevos": 20, "seguimientos": 10, "propuestas": 3,
             "reuniones": 3, "ventas_cerradas": 1000000, "cobro_diario": 1000000}

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS empresas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, nit TEXT, razon TEXT NOT NULL, razon_norm TEXT,
  tipo TEXT, ciiu TEXT, sector TEXT, depto TEXT, ciudad TEXT, web TEXT,
  activos REAL, pasivos REAL, patrimonio REAL, utilidad_op REAL,
  prioridad TEXT DEFAULT 'P3', prioridad_auto INTEGER DEFAULT 0, notas TEXT,
  filas_repetidas INTEGER DEFAULT 0, dup_revisado INTEGER DEFAULT 0, revisar TEXT,
  origen TEXT, creado TEXT);
CREATE INDEX IF NOT EXISTS ix_emp_nit ON empresas(nit);
CREATE INDEX IF NOT EXISTS ix_emp_norm ON empresas(razon_norm);
CREATE INDEX IF NOT EXISTS ix_emp_prio ON empresas(prioridad);
CREATE TABLE IF NOT EXISTS contactos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  nombre TEXT, cargo TEXT, telefono TEXT, correo TEXT, creado TEXT);
CREATE INDEX IF NOT EXISTS ix_con_emp ON contactos(empresa_id);
CREATE TABLE IF NOT EXISTS oportunidades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  etapa TEXT NOT NULL, valor_estimado REAL DEFAULT 0, responsable TEXT,
  ultima_interaccion TEXT, proximo_paso TEXT, proxima_fecha TEXT, caliente INTEGER DEFAULT 0,
  valor_cerrado REAL, fecha_cierre TEXT, motivo_perdida TEXT, creado TEXT, actualizado TEXT);
CREATE INDEX IF NOT EXISTS ix_op_emp ON oportunidades(empresa_id);
CREATE INDEX IF NOT EXISTS ix_op_etapa ON oportunidades(etapa);
CREATE TABLE IF NOT EXISTS historial_etapas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, oportunidad_id INTEGER NOT NULL REFERENCES oportunidades(id),
  etapa TEXT NOT NULL, fecha TEXT NOT NULL, creado TEXT);
CREATE INDEX IF NOT EXISTS ix_hist ON historial_etapas(etapa, fecha);
CREATE TABLE IF NOT EXISTS interacciones(
  id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  oportunidad_id INTEGER, contacto_id INTEGER, tipo TEXT NOT NULL, resultado TEXT, notas TEXT,
  fecha TEXT NOT NULL, creado TEXT, primer_contacto INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_int_fecha ON interacciones(fecha);
CREATE INDEX IF NOT EXISTS ix_int_emp ON interacciones(empresa_id);
CREATE TABLE IF NOT EXISTS cobros(
  id INTEGER PRIMARY KEY AUTOINCREMENT, oportunidad_id INTEGER NOT NULL REFERENCES oportunidades(id),
  monto REAL NOT NULL, fecha TEXT NOT NULL, nota TEXT, creado TEXT);
CREATE INDEX IF NOT EXISTS ix_cobro_fecha ON cobros(fecha);
CREATE TABLE IF NOT EXISTS metas(clave TEXT PRIMARY KEY, valor REAL);
CREATE TABLE IF NOT EXISTS importaciones(
  id INTEGER PRIMARY KEY AUTOINCREMENT, archivo TEXT, fecha TEXT, total INTEGER, nuevas INTEGER,
  duplicadas INTEGER, errores INTEGER, revisar INTEGER, detalle TEXT);
CREATE TABLE IF NOT EXISTS import_filas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, importacion_id INTEGER, fila INTEGER, empresa_id INTEGER,
  estado TEXT, motivo TEXT, datos TEXT);
CREATE INDEX IF NOT EXISTS ix_if_emp ON import_filas(empresa_id);
"""


# ---------------------------------------------------------------- utilidades
def ahora():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def hoy():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    for k, v in METAS_DEF.items():
        db.execute("INSERT OR IGNORE INTO metas(clave,valor) VALUES(?,?)", (k, v))
    db.commit()
    # Migración controlada: copia verificada ANTES de tocar una base que ya tiene datos.
    pendiente = MB.migracion_pendiente(db)
    if pendiente and db.execute("SELECT COUNT(*) FROM empresas").fetchone()[0] > 0:
        MS.hacer_respaldo(sys.modules[__name__], "pre_migracion")
    for m in (MB, MS, MD, MA):
        m.migrate(db)
    db.commit()
    if pendiente:
        MB.clasificar_todo(db)
        MB.puntuar_todo(db)
    db.close()


def rows(sql, args=()):
    return [dict(r) for r in get_db().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = get_db().execute(sql, args).fetchone()
    return dict(r) if r else None


class ApiError(Exception):
    def __init__(self, msg, code=400, extra=None):
        self.msg, self.code, self.extra = msg, code, extra or {}


@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify({"error": e.msg, **e.extra}), e.code


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


SUFIJOS = {"sas", "s a s", "sa", "s a", "ltda", "limitada", "e u", "eu", "sca", "s en c", "sucursal colombia"}


def razon_norm(s):
    t = " " + norm(s) + " "
    t = re.sub(r"\b(s a s|sas|s a|sa|ltda|limitada|e u|eu|s en c)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_nit(v):
    if v is None or v == "":
        return None
    if isinstance(v, float) and v == v:
        v = int(v)
    s = re.sub(r"\D", "", str(v).split("-")[0] if "-" in str(v) else str(v))
    return s or None


def num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(str(v).replace(".", "").replace(",", "."))
        except ValueError:
            return None


def body():
    return request.get_json(silent=True) or {}


def valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- empresas
EMP_COLS = ["nit", "razon", "tipo", "ciiu", "sector", "depto", "ciudad", "web", "activos",
            "pasivos", "patrimonio", "utilidad_op", "prioridad", "notas"]
EDIT_COLS = ["nit", "razon", "tipo", "ciiu", "sector", "depto", "ciudad", "web", "prioridad",
             "notas", "dup_revisado"]

BASE_SELECT = """SELECT e.*,
 (SELECT COUNT(*) FROM empresas x WHERE x.razon_norm=e.razon_norm AND x.id<>e.id AND e.razon_norm<>'') AS n_similares,
 (SELECT COUNT(*) FROM contactos c WHERE c.empresa_id=e.id) AS n_contactos,
 (SELECT o.id FROM oportunidades o WHERE o.empresa_id=e.id AND o.etapa NOT IN ('Ganado','Perdido') LIMIT 1) AS opp_activa,
 (SELECT o.etapa FROM oportunidades o WHERE o.empresa_id=e.id AND o.etapa NOT IN ('Ganado','Perdido') LIMIT 1) AS etapa_activa,
 (SELECT MAX(i.fecha) FROM interacciones i WHERE i.empresa_id=e.id) AS ultimo_contacto
 FROM empresas e"""


def filtros_empresas(a):
    w, p = [], []
    q = (a.get("q") or "").strip()
    if q:
        digits = re.sub(r"\D", "", q)
        parts = []
        for tok in norm(q).split():
            parts.append("(e.razon_norm LIKE ? OR LOWER(e.ciudad) LIKE ? OR LOWER(e.sector) LIKE ?)")
            p += [f"%{razon_norm(tok) or tok}%", f"%{tok}%", f"%{tok}%"]
        cond = " AND ".join(parts) if parts else "1=1"
        if digits and len(digits) >= 4:
            cond = f"({cond}) OR e.nit LIKE ? OR EXISTS(SELECT 1 FROM contactos c WHERE c.empresa_id=e.id AND (REPLACE(c.telefono,' ','') LIKE ?))"
            p += [f"{digits}%", f"%{digits}%"]
        elif parts:
            cond = f"({cond}) OR EXISTS(SELECT 1 FROM contactos c WHERE c.empresa_id=e.id AND (LOWER(c.nombre) LIKE ? OR LOWER(c.correo) LIKE ?))"
            p += [f"%{norm(q)}%", f"%{q.lower()}%"]
        w.append(f"({cond})")
    for col in ("prioridad", "depto", "ciudad", "tipo"):
        v = a.get(col)
        if v:
            w.append(f"e.{col}=?")
            p.append(v)
    if a.get("calidad"):
        w.append("e.calidad=?")
        p.append(a["calidad"])
    if a.get("min_score"):
        w.append("e.score>=?")
        p.append(float(a["min_score"]))
    if a.get("sector"):
        w.append("e.sector LIKE ?")
        p.append(f"%{a['sector']}%")
    if a.get("ciiu"):
        w.append("e.ciiu LIKE ?")
        p.append(f"{a['ciiu']}%")
    if a.get("dup") == "1":
        w.append("e.dup_revisado=0 AND (e.filas_repetidas>0 OR EXISTS(SELECT 1 FROM empresas x WHERE x.razon_norm=e.razon_norm AND x.id<>e.id AND e.razon_norm<>''))")
    if a.get("revisar") == "1":
        w.append("e.revisar IS NOT NULL AND e.revisar<>'' AND e.dup_revisado=0")
    if a.get("pipeline") == "con":
        w.append("EXISTS(SELECT 1 FROM oportunidades o WHERE o.empresa_id=e.id AND o.etapa NOT IN ('Ganado','Perdido'))")
    elif a.get("pipeline") == "sin":
        w.append("NOT EXISTS(SELECT 1 FROM oportunidades o WHERE o.empresa_id=e.id)")
    if a.get("contacto") == "con":
        w.append("EXISTS(SELECT 1 FROM contactos c WHERE c.empresa_id=e.id)")
    elif a.get("contacto") == "sin":
        w.append("NOT EXISTS(SELECT 1 FROM contactos c WHERE c.empresa_id=e.id)")
    return (" WHERE " + " AND ".join(w)) if w else "", p


SORTS = {"razon": "e.razon_norm", "activos": "e.activos DESC", "prioridad": "e.prioridad, e.activos DESC",
         "reciente": "e.id DESC", "score": "e.score DESC", "ciudad": "e.ciudad, e.razon_norm"}


@app.get("/api/empresas")
def listar_empresas():
    a = request.args
    where, p = filtros_empresas(a)
    total = one(f"SELECT COUNT(*) n FROM empresas e{where}", p)["n"]
    per = min(int(a.get("per", 50)), 200)
    page = max(int(a.get("page", 1)), 1)
    order = SORTS.get(a.get("orden"), "e.razon_norm")
    data = rows(f"{BASE_SELECT}{where} ORDER BY {order} LIMIT ? OFFSET ?", p + [per, (page - 1) * per])
    return jsonify({"total": total, "page": page, "per": per, "items": data})


@app.get("/api/empresas/facetas")
def facetas():
    return jsonify({
        "deptos": [r["depto"] for r in rows("SELECT DISTINCT depto FROM empresas WHERE depto<>'' ORDER BY depto")],
        "tipos": [r["tipo"] for r in rows("SELECT DISTINCT tipo FROM empresas WHERE tipo<>'' ORDER BY tipo")],
        "responsables": [r["responsable"] for r in rows("SELECT DISTINCT responsable FROM oportunidades WHERE responsable<>'' ORDER BY responsable")],
        "etapas": ETAPAS, "tipos_interaccion": TIPOS_INT,
        "totales": one("SELECT COUNT(*) empresas, (SELECT COUNT(*) FROM contactos) contactos, (SELECT COUNT(*) FROM oportunidades) oportunidades FROM empresas"),
    })


@app.get("/api/empresas/ciudades")
def ciudades():
    d = request.args.get("depto")
    if d:
        r = rows("SELECT DISTINCT ciudad FROM empresas WHERE depto=? AND ciudad<>'' ORDER BY ciudad", (d,))
    else:
        r = rows("SELECT ciudad, COUNT(*) n FROM empresas WHERE ciudad<>'' GROUP BY ciudad ORDER BY n DESC LIMIT 60")
    return jsonify([x["ciudad"] for x in r])


@app.get("/api/empresas/exportar")
def exportar():
    where, p = filtros_empresas(request.args)
    data = rows(f"{BASE_SELECT}{where} ORDER BY e.razon_norm", p)
    out = io.StringIO()
    cols = ["id", "nit", "razon", "tipo", "prioridad", "ciiu", "sector", "depto", "ciudad", "activos",
            "patrimonio", "utilidad_op", "n_contactos", "etapa_activa", "ultimo_contacto", "notas"]
    w = csv.writer(out)
    w.writerow(cols)
    for r in data:
        w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=veltrix_empresas_{hoy()}.csv"})


def validar_empresa(d, parcial=False):
    out = {}
    for c in EDIT_COLS:
        if c in d:
            v = d[c]
            out[c] = v.strip() if isinstance(v, str) else v
    if "nit" in out:
        out["nit"] = clean_nit(out["nit"])
    if not parcial and not out.get("razon"):
        raise ApiError("La razón social es obligatoria.")
    if "razon" in out and not out["razon"]:
        raise ApiError("La razón social es obligatoria.")
    if "prioridad" in out and out["prioridad"] not in ("P1", "P2", "P3"):
        raise ApiError("La prioridad debe ser P1, P2 o P3.")
    for c in ("activos", "pasivos", "patrimonio", "utilidad_op"):
        if c in d:
            out[c] = num(d[c])
    if "razon" in out:
        out["razon_norm"] = razon_norm(out["razon"])
    return out


@app.post("/api/empresas")
def crear_empresa():
    d = validar_empresa(body())
    d.setdefault("prioridad", "P3")
    d["origen"], d["creado"] = "manual", ahora()
    if d.get("nit") and one("SELECT id FROM empresas WHERE nit=?", (d["nit"],)):
        ex = one("SELECT id,razon FROM empresas WHERE nit=?", (d["nit"],))
        raise ApiError(f"Ya existe una empresa con ese NIT: {ex['razon']}", 409, {"existente": ex["id"]})
    db = get_db()
    cols = ",".join(d)
    cur = db.execute(f"INSERT INTO empresas({cols}) VALUES({','.join('?' * len(d))})", list(d.values()))
    eid = cur.lastrowid
    c = body().get("contacto")
    if c and any(c.get(k) for k in ("nombre", "telefono", "correo")):
        db.execute("INSERT INTO contactos(empresa_id,nombre,cargo,telefono,correo,creado) VALUES(?,?,?,?,?,?)",
                   (eid, c.get("nombre"), c.get("cargo"), c.get("telefono"), c.get("correo"), ahora()))
    db.commit()
    MB.clasificar_todo(db, solo_nulos=True)
    MB.puntuar_todo(db, solo_nulos=True)
    return jsonify({"id": eid}), 201


@app.get("/api/empresas/<int:eid>")
def ver_empresa(eid):
    e = one(f"{BASE_SELECT} WHERE e.id=?", (eid,))
    if not e:
        raise ApiError("Empresa no encontrada.", 404)
    e["contactos"] = rows("SELECT * FROM contactos WHERE empresa_id=? ORDER BY id", (eid,))
    e["oportunidades"] = rows("""SELECT o.*, COALESCE((SELECT SUM(monto) FROM cobros c WHERE c.oportunidad_id=o.id),0) cobrado
        FROM oportunidades o WHERE empresa_id=? ORDER BY o.id DESC""", (eid,))
    e["interacciones"] = rows("""SELECT i.*, c.nombre contacto FROM interacciones i LEFT JOIN contactos c ON c.id=i.contacto_id
        WHERE i.empresa_id=? ORDER BY i.fecha DESC, i.id DESC LIMIT 100""", (eid,))
    e["similares"] = rows("SELECT id,nit,razon,ciudad FROM empresas WHERE razon_norm=? AND id<>? AND razon_norm<>''",
                          (e["razon_norm"], eid))
    e["filas_origen"] = rows("SELECT fila,estado,motivo,datos FROM import_filas WHERE empresa_id=? ORDER BY fila", (eid,))
    return jsonify(e)


@app.put("/api/empresas/<int:eid>")
def editar_empresa(eid):
    if not one("SELECT id FROM empresas WHERE id=?", (eid,)):
        raise ApiError("Empresa no encontrada.", 404)
    d = validar_empresa(body(), parcial=True)
    if "prioridad" in d:
        d["prioridad_auto"] = 0
    if d.get("nit") and one("SELECT id FROM empresas WHERE nit=? AND id<>?", (d["nit"], eid)):
        raise ApiError("Otra empresa ya tiene ese NIT.", 409)
    if d:
        old = one("SELECT * FROM empresas WHERE id=?", (eid,))
        MB.audit_cambios(get_db(), "empresa", eid, old, d)
        get_db().execute(f"UPDATE empresas SET {','.join(k + '=?' for k in d)} WHERE id=?", list(d.values()) + [eid])
        get_db().commit()
        if any(k in d for k in ("razon", "nit", "ciudad", "depto", "ciiu", "dup_revisado")):
            MB.clasificar_todo(get_db(), ids=[eid])
            MB.puntuar_todo(get_db(), ids=[eid])
    return jsonify({"ok": True})


@app.delete("/api/empresas/<int:eid>")
def borrar_empresa(eid):
    if one("SELECT 1 x FROM oportunidades WHERE empresa_id=? UNION SELECT 1 FROM interacciones WHERE empresa_id=?", (eid, eid)):
        raise ApiError("No se puede borrar: la empresa tiene oportunidades o seguimientos. Cambie su prioridad o añada una nota.", 409)
    db = get_db()
    db.execute("DELETE FROM contactos WHERE empresa_id=?", (eid,))
    db.execute("UPDATE import_filas SET empresa_id=NULL, estado='empresa_borrada' WHERE empresa_id=?", (eid,))
    db.execute("DELETE FROM empresas WHERE id=?", (eid,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- contactos
@app.post("/api/empresas/<int:eid>/contactos")
def crear_contacto(eid):
    d = body()
    if not any((d.get(k) or "").strip() for k in ("nombre", "telefono", "correo")):
        raise ApiError("Indique al menos nombre, teléfono o correo.")
    if d.get("correo") and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", d["correo"].strip()):
        raise ApiError("El correo no parece válido.")
    if not one("SELECT id FROM empresas WHERE id=?", (eid,)):
        raise ApiError("Empresa no encontrada.", 404)
    cur = get_db().execute("INSERT INTO contactos(empresa_id,nombre,cargo,telefono,correo,creado) VALUES(?,?,?,?,?,?)",
                           (eid, *(((d.get(k) or "").strip() or None) for k in ("nombre", "cargo", "telefono", "correo")), ahora()))
    get_db().commit()
    return jsonify({"id": cur.lastrowid}), 201


@app.put("/api/contactos/<int:cid>")
def editar_contacto(cid):
    d = body()
    if d.get("correo") and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", d["correo"].strip()):
        raise ApiError("El correo no parece válido.")
    get_db().execute("UPDATE contactos SET nombre=?,cargo=?,telefono=?,correo=? WHERE id=?",
                     (*(((d.get(k) or "").strip() or None) for k in ("nombre", "cargo", "telefono", "correo")), cid))
    get_db().commit()
    return jsonify({"ok": True})


@app.delete("/api/contactos/<int:cid>")
def borrar_contacto(cid):
    get_db().execute("UPDATE interacciones SET contacto_id=NULL WHERE contacto_id=?", (cid,))
    get_db().execute("DELETE FROM contactos WHERE id=?", (cid,))
    get_db().commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- oportunidades
OPP_SELECT = """SELECT o.*, e.razon, e.nit, e.prioridad, e.ciudad, e.sector,
 COALESCE((SELECT SUM(monto) FROM cobros c WHERE c.oportunidad_id=o.id),0) AS cobrado,
 (SELECT nombre FROM contactos c WHERE c.empresa_id=e.id ORDER BY id LIMIT 1) AS contacto,
 (SELECT telefono FROM contactos c WHERE c.empresa_id=e.id AND telefono<>'' ORDER BY id LIMIT 1) AS telefono
 FROM oportunidades o JOIN empresas e ON e.id=o.empresa_id"""


def es_caliente(o):
    return bool(o["caliente"]) or o["etapa"] in CALIENTES


def decorar(o):
    o["es_caliente"] = es_caliente(o) and o["etapa"] in ACTIVAS
    t = hoy()
    o["vencido"] = bool(o["etapa"] in ACTIVAS and o["proxima_fecha"] and o["proxima_fecha"] < t)
    o["sin_proximo_paso"] = bool(o["etapa"] in ACTIVAS and (not o["proximo_paso"] or not o["proxima_fecha"]))
    if o["etapa"] == "Ganado":
        o["saldo"] = (o["valor_cerrado"] or 0) - o["cobrado"]
    return o


def registrar_etapa(oid, etapa):
    get_db().execute("INSERT INTO historial_etapas(oportunidad_id,etapa,fecha,creado) VALUES(?,?,?,?)",
                     (oid, etapa, hoy(), ahora()))


def exigir_paso(proximo_paso, fecha):
    if not (proximo_paso or "").strip():
        raise ApiError("Toda oportunidad activa necesita un próximo paso.")
    if not valid_date(fecha):
        raise ApiError("Indique la fecha del próximo paso (AAAA-MM-DD).")


@app.get("/api/oportunidades")
def listar_opps():
    w, p = [], []
    a = request.args
    if a.get("etapa"):
        w.append("o.etapa=?")
        p.append(a["etapa"])
    if a.get("responsable"):
        w.append("o.responsable=?")
        p.append(a["responsable"])
    if a.get("q"):
        w.append("e.razon_norm LIKE ?")
        p.append(f"%{razon_norm(a['q']) or norm(a['q'])}%")
    if a.get("cerradas") != "1":
        # tablero: activas + cerradas recientes (30 dias) para ver cobros
        w.append("(o.etapa NOT IN ('Ganado','Perdido') OR o.fecha_cierre>=?)")
        p.append((datetime.now(TZ) - timedelta(days=30)).strftime("%Y-%m-%d"))
    where = (" WHERE " + " AND ".join(w)) if w else ""
    data = [decorar(o) for o in rows(f"{OPP_SELECT}{where} ORDER BY o.proxima_fecha IS NULL, o.proxima_fecha, o.id", p)]
    return jsonify(data)


@app.post("/api/oportunidades")
def crear_opp():
    d = body()
    eid = d.get("empresa_id")
    if not one("SELECT id FROM empresas WHERE id=?", (eid,)):
        raise ApiError("Empresa no encontrada.", 404)
    ex = one("SELECT id FROM oportunidades WHERE empresa_id=? AND etapa NOT IN ('Ganado','Perdido')", (eid,))
    if ex:
        raise ApiError("Esta empresa ya tiene una oportunidad activa.", 409, {"existente": ex["id"]})
    etapa = d.get("etapa") or "Por contactar"
    if etapa not in ACTIVAS:
        raise ApiError("Etapa inicial no válida.")
    paso = d.get("proximo_paso") or ("Primer contacto" if etapa == "Por contactar" else "")
    fecha = d.get("proxima_fecha") or (hoy() if etapa == "Por contactar" else None)
    exigir_paso(paso, fecha)
    cur = get_db().execute("""INSERT INTO oportunidades(empresa_id,etapa,valor_estimado,responsable,proximo_paso,proxima_fecha,caliente,creado,actualizado)
        VALUES(?,?,?,?,?,?,?,?,?)""", (eid, etapa, num(d.get("valor_estimado")) or 0, (d.get("responsable") or "").strip() or None,
                                       paso.strip(), fecha, 1 if d.get("caliente") else 0, ahora(), ahora()))
    registrar_etapa(cur.lastrowid, etapa)
    get_db().commit()
    return jsonify({"id": cur.lastrowid}), 201


def get_opp(oid):
    o = one(f"{OPP_SELECT} WHERE o.id=?", (oid,))
    if not o:
        raise ApiError("Oportunidad no encontrada.", 404)
    return o


@app.get("/api/oportunidades/<int:oid>")
def ver_opp(oid):
    o = decorar(get_opp(oid))
    o["cobros"] = rows("SELECT * FROM cobros WHERE oportunidad_id=? ORDER BY fecha DESC, id DESC", (oid,))
    o["historial"] = rows("SELECT etapa,fecha FROM historial_etapas WHERE oportunidad_id=? ORDER BY id", (oid,))
    return jsonify(o)


@app.put("/api/oportunidades/<int:oid>")
def editar_opp(oid):
    o = get_opp(oid)
    d = body()
    upd = {}
    for k in ("valor_estimado",):
        if k in d:
            upd[k] = num(d[k]) or 0
    if "responsable" in d:
        upd["responsable"] = (d["responsable"] or "").strip() or None
    if "caliente" in d:
        upd["caliente"] = 1 if d["caliente"] else 0
    if "valor_cerrado" in d and o["etapa"] == "Ganado":
        v = num(d["valor_cerrado"])
        if v is None or v <= 0:
            raise ApiError("El valor cerrado debe ser mayor que cero.")
        if v < o["cobrado"]:
            raise ApiError("El valor cerrado no puede ser menor que lo ya cobrado.")
        upd["valor_cerrado"] = v
    if "proximo_paso" in d or "proxima_fecha" in d:
        paso = d.get("proximo_paso", o["proximo_paso"])
        fecha = d.get("proxima_fecha", o["proxima_fecha"])
        if o["etapa"] in ACTIVAS:
            exigir_paso(paso, fecha)
        upd["proximo_paso"], upd["proxima_fecha"] = (paso or "").strip(), fecha
    if upd:
        upd["actualizado"] = ahora()
        get_db().execute(f"UPDATE oportunidades SET {','.join(k + '=?' for k in upd)} WHERE id=?", list(upd.values()) + [oid])
        get_db().commit()
    return jsonify({"ok": True})


@app.post("/api/oportunidades/<int:oid>/mover")
def mover_opp(oid):
    o = get_opp(oid)
    d = body()
    etapa = d.get("etapa")
    if etapa not in ETAPAS:
        raise ApiError("Etapa no válida.")
    if o["etapa"] in ("Ganado", "Perdido") and etapa in ACTIVAS and not d.get("reabrir"):
        raise ApiError("La oportunidad está cerrada. Reábrala explícitamente para moverla.")
    if etapa == o["etapa"]:
        return jsonify({"ok": True, "sin_cambio": True})
    upd = {"etapa": etapa, "actualizado": ahora()}
    if etapa in ACTIVAS:
        paso = d.get("proximo_paso") or o["proximo_paso"]
        fecha = d.get("proxima_fecha") or o["proxima_fecha"]
        exigir_paso(paso, fecha)
        upd.update(proximo_paso=paso.strip(), proxima_fecha=fecha, fecha_cierre=None, valor_cerrado=None, motivo_perdida=None)
    elif etapa == "Ganado":
        v = num(d.get("valor_cerrado")) or o["valor_estimado"]
        if not v or v <= 0:
            raise ApiError("Indique el valor cerrado para marcar la venta como ganada.")
        upd.update(valor_cerrado=v, fecha_cierre=d.get("fecha_cierre") or hoy(), proximo_paso=None, proxima_fecha=None, motivo_perdida=None)
    else:  # Perdido
        upd.update(fecha_cierre=d.get("fecha_cierre") or hoy(), motivo_perdida=(d.get("motivo") or "").strip() or None,
                   proximo_paso=None, proxima_fecha=None, valor_cerrado=None)
        if o["cobrado"] > 0:
            raise ApiError("No se puede marcar como perdida: ya tiene cobros registrados.")
    if etapa in ACTIVAS and o["etapa"] == "Ganado" and o["cobrado"] > 0:
        raise ApiError("No se puede reabrir una venta con cobros registrados.")
    get_db().execute(f"UPDATE oportunidades SET {','.join(k + '=?' for k in upd)} WHERE id=?", list(upd.values()) + [oid])
    registrar_etapa(oid, etapa)
    get_db().commit()
    return jsonify({"ok": True})


@app.post("/api/oportunidades/<int:oid>/cobros")
def registrar_cobro(oid):
    o = get_opp(oid)
    d = body()
    if o["etapa"] != "Ganado":
        raise ApiError("Solo se registran cobros sobre ventas ganadas.")
    m = num(d.get("monto"))
    if not m or m <= 0:
        raise ApiError("El monto cobrado debe ser mayor que cero.")
    saldo = (o["valor_cerrado"] or 0) - o["cobrado"]
    if m > saldo + 0.005:
        raise ApiError(f"El cobro supera el saldo pendiente ({saldo:,.0f}). Ajuste el valor cerrado si corresponde.")
    f = d.get("fecha") or hoy()
    if not valid_date(f):
        raise ApiError("Fecha de cobro no válida.")
    did = d.get("documento_id") or None
    if did:
        doc = one("SELECT * FROM documentos WHERE id=? AND oportunidad_id=? AND tipo='cuenta_cobro' AND estado<>'anulado'", (did, oid))
        if not doc:
            raise ApiError("La cuenta de cobro indicada no corresponde a esta venta.")
        pend = doc["valor"] - one("SELECT COALESCE(SUM(monto),0) n FROM cobros WHERE documento_id=?", (did,))["n"]
        if m > pend + 0.005:
            raise ApiError(f"El cobro supera el saldo de la cuenta de cobro {doc['numero']} ({pend:,.0f}).")
    cur = get_db().execute("INSERT INTO cobros(oportunidad_id,monto,fecha,nota,creado,documento_id) VALUES(?,?,?,?,?,?)",
                           (oid, m, f, (d.get("nota") or "").strip() or None, ahora(), did))
    get_db().commit()
    if did:
        recalcular_pago_doc(did)
    return jsonify({"id": cur.lastrowid}), 201


@app.delete("/api/cobros/<int:cid>")
def borrar_cobro(cid):
    c = one("SELECT documento_id FROM cobros WHERE id=?", (cid,))
    get_db().execute("DELETE FROM cobros WHERE id=?", (cid,))
    get_db().commit()
    if c and c["documento_id"]:
        recalcular_pago_doc(c["documento_id"])
    return jsonify({"ok": True})


# ---------------------------------------------------------------- seguimientos
@app.post("/api/interacciones")
def crear_interaccion():
    d = body()
    eid = d.get("empresa_id")
    if not one("SELECT id FROM empresas WHERE id=?", (eid,)):
        raise ApiError("Empresa no encontrada.", 404)
    tipo = d.get("tipo")
    if tipo not in TIPOS_INT:
        raise ApiError("Elija el tipo de contacto.")
    if not (d.get("resultado") or "").strip():
        raise ApiError("Registre el resultado del contacto.")
    db = get_db()
    opp = None
    if d.get("oportunidad_id"):
        opp = one("SELECT * FROM oportunidades WHERE id=? AND empresa_id=?", (d["oportunidad_id"], eid))
    if not opp:
        opp = one("SELECT * FROM oportunidades WHERE empresa_id=? AND etapa NOT IN ('Ganado','Perdido')", (eid,))
    activa = opp is None or opp["etapa"] in ACTIVAS
    paso = (d.get("proxima_accion") or "").strip()
    fecha = d.get("proxima_fecha")
    if activa:
        exigir_paso(paso, fecha)
    primer = 0 if one("SELECT 1 x FROM interacciones WHERE empresa_id=?", (eid,)) else 1
    f = d.get("fecha") or hoy()
    if not valid_date(f):
        raise ApiError("Fecha no válida.")
    if opp is None:
        cur = db.execute("""INSERT INTO oportunidades(empresa_id,etapa,valor_estimado,responsable,creado,actualizado)
            VALUES(?,?,?,?,?,?)""", (eid, "Contactado", num(d.get("valor_estimado")) or 0,
                                     (d.get("responsable") or "").strip() or None, ahora(), ahora()))
        oid = cur.lastrowid
        registrar_etapa(oid, "Contactado")
    else:
        oid = opp["id"]
        if opp["etapa"] == "Por contactar":
            db.execute("UPDATE oportunidades SET etapa='Contactado' WHERE id=?", (oid,))
            registrar_etapa(oid, "Contactado")
    if activa:
        db.execute("UPDATE oportunidades SET proximo_paso=?, proxima_fecha=?, ultima_interaccion=?, actualizado=? WHERE id=?",
                   (paso, fecha, f, ahora(), oid))
    else:
        db.execute("UPDATE oportunidades SET ultima_interaccion=?, actualizado=? WHERE id=?", (f, ahora(), oid))
    cur = db.execute("""INSERT INTO interacciones(empresa_id,oportunidad_id,contacto_id,tipo,resultado,notas,fecha,creado,primer_contacto)
        VALUES(?,?,?,?,?,?,?,?,?)""", (eid, oid, d.get("contacto_id") or None, tipo, d["resultado"].strip(),
                                       (d.get("notas") or "").strip() or None, f, ahora(), primer))
    db.commit()
    return jsonify({"id": cur.lastrowid, "oportunidad_id": oid, "primer_contacto": bool(primer)}), 201


@app.get("/api/seguimientos")
def seguimientos():
    t = hoy()
    vista = request.args.get("vista", "todos")
    cond = "o.etapa IN ('Por contactar','Contactado','Conversación iniciada','Calificado','Reunión agendada','Propuesta enviada','Negociación')"
    p = []
    if vista == "vencidos":
        cond += " AND o.proxima_fecha<?"
        p.append(t)
    elif vista == "hoy":
        cond += " AND o.proxima_fecha=?"
        p.append(t)
    elif vista == "proximos":
        cond += " AND o.proxima_fecha>=? AND o.proxima_fecha<=?"
        p += [t, (datetime.now(TZ) + timedelta(days=7)).strftime("%Y-%m-%d")]
    elif vista == "sin_paso":
        cond += " AND (o.proximo_paso IS NULL OR o.proximo_paso='' OR o.proxima_fecha IS NULL)"
    data = [decorar(o) for o in rows(f"{OPP_SELECT} WHERE {cond} ORDER BY o.proxima_fecha IS NULL DESC, o.proxima_fecha, o.id", p)]
    return jsonify(data)


@app.get("/api/interacciones")
def listar_interacciones():
    desde = request.args.get("desde") or (datetime.now(TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
    return jsonify(rows("""SELECT i.*, e.razon, c.nombre contacto FROM interacciones i
        JOIN empresas e ON e.id=i.empresa_id LEFT JOIN contactos c ON c.id=i.contacto_id
        WHERE i.fecha>=? ORDER BY i.fecha DESC, i.id DESC LIMIT 300""", (desde,)))


# ---------------------------------------------------------------- HOY
@app.get("/api/hoy")
def vista_hoy():
    t = hoy()
    act = [decorar(o) for o in rows(f"{OPP_SELECT} WHERE o.etapa IN ({','.join('?' * len(ACTIVAS))})", ACTIVAS)]
    calientes = [o for o in act if o["es_caliente"]]
    calientes.sort(key=lambda o: (o["proxima_fecha"] or "0000", -ACTIVAS.index(o["etapa"])))
    ids_cal = {o["id"] for o in calientes}
    vencidos = sorted([o for o in act if o["vencido"] and o["id"] not in ids_cal], key=lambda o: o["proxima_fecha"])
    programados = [o for o in act if o["proxima_fecha"] == t and o["id"] not in ids_cal and o["etapa"] != "Por contactar"]
    por_contactar = [o for o in act if o["etapa"] == "Por contactar" and o["id"] not in ids_cal and (o["proxima_fecha"] or t) <= t]
    sin_paso = [o for o in act if o["sin_proximo_paso"]]
    metas = {r["clave"]: r["valor"] for r in rows("SELECT * FROM metas")}
    hechos = one("SELECT COALESCE(SUM(primer_contacto),0) n FROM interacciones WHERE fecha=?", (t,))["n"]
    faltan = max(int(metas.get("contactos_nuevos", 20)) - int(hechos), 0)
    nuevas = MB.prospectar(sys.modules[__name__], max(faltan, 5))
    return jsonify({"fecha": t, "calientes": calientes, "vencidos": vencidos, "programados": programados,
                    "por_contactar": por_contactar, "nuevas": nuevas, "sin_paso": sin_paso,
                    "resumen": dashboard_data(t)["hoy"]})


# ---------------------------------------------------------------- dashboard
def contar_dia(f):
    c = lambda sql, a=(): one(sql, a)["n"]
    return {
        "contactos_nuevos": c("SELECT COALESCE(SUM(primer_contacto),0) n FROM interacciones WHERE fecha=?", (f,)),
        "seguimientos": c("SELECT COUNT(*) n FROM interacciones WHERE fecha=? AND primer_contacto=0", (f,)),
        "conversaciones": c("SELECT COUNT(DISTINCT oportunidad_id) n FROM historial_etapas WHERE etapa='Conversación iniciada' AND fecha=?", (f,)),
        "reuniones": c("SELECT COUNT(DISTINCT oportunidad_id) n FROM historial_etapas WHERE etapa='Reunión agendada' AND fecha=?", (f,)),
        "propuestas": c("SELECT COUNT(DISTINCT oportunidad_id) n FROM historial_etapas WHERE etapa='Propuesta enviada' AND fecha=?", (f,)),
        "cierres": c("SELECT COUNT(*) n FROM oportunidades WHERE etapa='Ganado' AND fecha_cierre=?", (f,)),
        "vendido": c("SELECT COALESCE(SUM(valor_cerrado),0) n FROM oportunidades WHERE etapa='Ganado' AND fecha_cierre=?", (f,)),
        "cobrado": c("SELECT COALESCE(SUM(monto),0) n FROM cobros WHERE fecha=?", (f,)),
    }


def dashboard_data(f):
    metas = {r["clave"]: r["valor"] for r in rows("SELECT * FROM metas")}
    dias = []
    d0 = datetime.strptime(f, "%Y-%m-%d")
    for i in range(6, -1, -1):
        fd = (d0 - timedelta(days=i)).strftime("%Y-%m-%d")
        dias.append({"fecha": fd, **contar_dia(fd)})
    mes = f[:7]
    embudo = []
    for e in ETAPAS[:7]:
        r = one("""SELECT COUNT(*) n, COALESCE(SUM(valor_estimado),0) v FROM oportunidades WHERE etapa=?""", (e,))
        embudo.append({"etapa": e, "n": r["n"], "valor": r["v"]})
    activas = one("SELECT COUNT(*) n, COALESCE(SUM(valor_estimado),0) v FROM oportunidades WHERE etapa NOT IN ('Ganado','Perdido')")
    cerr = one("""SELECT COUNT(*) n, COALESCE(SUM(valor_cerrado),0) v FROM oportunidades WHERE etapa='Ganado'""")
    cob = one("SELECT COALESCE(SUM(monto),0) v FROM cobros")
    mesd = {
        "vendido": one("SELECT COALESCE(SUM(valor_cerrado),0) n FROM oportunidades WHERE etapa='Ganado' AND substr(fecha_cierre,1,7)=?", (mes,))["n"],
        "cobrado": one("SELECT COALESCE(SUM(monto),0) n FROM cobros WHERE substr(fecha,1,7)=?", (mes,))["n"],
    }
    return {"fecha": f, "metas": metas, "hoy": contar_dia(f), "dias": dias, "embudo": embudo,
            "activas": {"n": activas["n"], "valor": activas["v"]},
            "ganadas": {"n": cerr["n"], "valor": cerr["v"], "cobrado": cob["v"], "por_cobrar": cerr["v"] - cob["v"]},
            "perdidas": one("SELECT COUNT(*) n FROM oportunidades WHERE etapa='Perdido'")["n"],
            "mes": mesd, "sin_paso": one("""SELECT COUNT(*) n FROM oportunidades WHERE etapa NOT IN ('Ganado','Perdido')
               AND (proximo_paso IS NULL OR proximo_paso='' OR proxima_fecha IS NULL)""")["n"],
            "vencidos": one("SELECT COUNT(*) n FROM oportunidades WHERE etapa NOT IN ('Ganado','Perdido') AND proxima_fecha<?", (f,))["n"]}


@app.get("/api/dashboard")
def dashboard():
    f = request.args.get("fecha") or hoy()
    if not valid_date(f):
        raise ApiError("Fecha no válida.")
    return jsonify(dashboard_data(f))


@app.get("/api/metas")
def get_metas():
    return jsonify({r["clave"]: r["valor"] for r in rows("SELECT * FROM metas")})


@app.put("/api/metas")
def put_metas():
    for k, v in body().items():
        if k in METAS_DEF:
            n = num(v)
            if n is None or n < 0:
                raise ApiError(f"Meta no válida: {k}")
            get_db().execute("INSERT OR REPLACE INTO metas(clave,valor) VALUES(?,?)", (k, n))
    get_db().commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- importacion
ALIAS = {
    "nit": ["nit", "identificacion", "nit empresa", "documento"],
    "razon": ["razon social", "razon social de la sociedad", "empresa", "nombre empresa", "nombre", "compania", "company"],
    "tipo": ["tipo de empresa", "tipo", "tamano"],
    "ciiu": ["ciiu", "actividad", "actividad economica", "codigo ciiu"],
    "sector": ["sector", "industria"],
    "depto": ["departamento", "depto"],
    "ciudad": ["ciudad", "municipio"],
    "web": ["web", "sitio web", "pagina web", "website"],
    "prioridad": ["prioridad", "clasificacion", "p"],
    "notas": ["notas", "observaciones", "comentarios"],
    "c_nombre": ["contacto", "nombre contacto", "persona de contacto", "nombre del contacto"],
    "c_cargo": ["cargo", "rol"],
    "c_telefono": ["telefono", "celular", "movil", "tel", "whatsapp", "telefono contacto"],
    "c_correo": ["correo", "email", "e mail", "correo electronico", "mail"],
}
REGEX_ALIAS = [
    ("activos", r"^total de activos assets|^total activos$|^activos totales$|^activos$"),
    ("pasivos", r"^total pasivos liabilities|^total pasivos$|^pasivos$"),
    ("patrimonio", r"^patrimonio total|^patrimonio$"),
    ("utilidad_op", r"ganancia perdida por actividades de operacion|^utilidad operacional$|^utilidad$"),
    ("ciiu", r"^clasificacion industrial internacional uniforme"),
    ("depto", r"^departamento de la direccion"),
    ("ciudad", r"^ciudad de la direccion"),
    ("razon", r"^razon social"),
]


def mapear_columnas(headers):
    m = {}
    nh = [norm(h) for h in headers]
    for i, h in enumerate(nh):
        for campo, rx in REGEX_ALIAS:
            if campo not in m and re.search(rx, h):
                m[campo] = i
    for campo, als in ALIAS.items():
        if campo in m:
            continue
        for i, h in enumerate(nh):
            if h in als and i not in m.values():
                m[campo] = i
                break
    return m


def leer_archivo(f):
    nombre = (f.filename or "").lower()
    if nombre.endswith(".csv") or nombre.endswith(".txt"):
        raw = f.read()
        for enc in ("utf-8-sig", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        it = list(csv.reader(io.StringIO(text), dialect))
        return it
    if nombre.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    raise ApiError("Formato no soportado. Use CSV o Excel (.xlsx).")


def sector_de(ciiu):
    if ciiu and " - " in ciiu:
        return ciiu.split(" - ", 1)[1].strip()
    return None


def cod_ciiu(ciiu):
    if ciiu and " - " in ciiu:
        return ciiu.split(" - ", 1)[0].strip()
    return ciiu


@app.post("/api/importar")
def importar():
    f = request.files.get("archivo")
    if not f:
        raise ApiError("Adjunte un archivo CSV o Excel.")
    preview = request.form.get("preview") == "1"
    data = leer_archivo(f)
    data = [r for r in data if r is not None]
    if not data:
        raise ApiError("El archivo está vacío.")
    headers = ["" if h is None else str(h) for h in data[0]]
    m = mapear_columnas(headers)
    if "razon" not in m:
        raise ApiError("No se encontró la columna de razón social / nombre de empresa.",
                       extra={"columnas": headers})
    db = get_db()
    existentes_nit = {r["nit"]: r["id"] for r in rows("SELECT id,nit FROM empresas WHERE nit IS NOT NULL")}
    existentes_norm = {}
    for r in rows("SELECT id,razon_norm FROM empresas"):
        existentes_norm.setdefault(r["razon_norm"], r["id"])
    get = lambda r, k: (r[m[k]] if k in m and m[k] < len(r) else None)
    vistos_nit = {}      # nit -> empresa_id
    vistos_norm = {}
    nuevas, dups, errs, revisar = [], [], [], []
    candidatas = []
    for n, r in enumerate(data[1:], start=2):
        r = list(r)
        if all(v in (None, "") for v in r):
            errs.append((n, "Fila vacía", r))
            continue
        razon = (str(get(r, "razon") or "")).strip()
        nit = clean_nit(get(r, "nit")) if "nit" in m else None
        if not razon:
            errs.append((n, "Sin razón social", r))
            continue
        if "nit" in m and get(r, "nit") not in (None, "") and not nit:
            errs.append((n, "NIT no numérico", r))
            continue
        candidatas.append((n, r, razon, nit))
    # umbrales de prioridad automatica (por activos) solo si el archivo no trae prioridad
    umbral = None
    if "prioridad" not in m and "activos" in m:
        vals = sorted(v for v in (num(get(c[1], "activos")) for c in candidatas) if v and v > 0)
        if vals:
            umbral = (vals[int(len(vals) * 0.90)], vals[int(len(vals) * 0.60)])
    tcol = ahora()
    registros = []  # (fila, empresa_dict, contacto, estado, motivo)
    nuevos_ids_tmp = 0
    for n, r, razon, nit in candidatas:
        rn = razon_norm(razon)
        if nit and nit in existentes_nit:
            dups.append((n, f"NIT ya existe en el CRM (no se sobrescribió)", r, existentes_nit[nit]))
            continue
        if nit and nit in vistos_nit:
            dups.append((n, "NIT repetido en el archivo (se conserva la primera fila)", r, vistos_nit[nit]))
            continue
        if not nit and rn in existentes_norm:
            dups.append((n, "Misma razón social ya existe (sin NIT)", r, existentes_norm[rn]))
            continue
        ciiu_raw = (str(get(r, "ciiu") or "")).strip() or None
        activos = num(get(r, "activos"))
        pr = (str(get(r, "prioridad") or "")).strip().upper()
        auto = 0
        if pr in ("P1", "P2", "P3"):
            pass
        elif umbral and activos is not None:
            pr, auto = ("P1" if activos >= umbral[0] else "P2" if activos >= umbral[1] else "P3"), 1
        else:
            pr = "P3"
        flags = []
        if "activos" in m and (activos is None or activos <= 0):
            flags.append("Activos en cero o vacío")
        if "ciudad" in m and not (get(r, "ciudad") or "").__str__().strip():
            flags.append("Sin ciudad")
        if rn in existentes_norm and existentes_norm[rn]:
            flags.append("Razón social similar a otra empresa")
        elif rn in vistos_norm:
            flags.append("Razón social similar a otra empresa del archivo")
        emp = dict(nit=nit, razon=razon, razon_norm=rn, tipo=(str(get(r, "tipo") or "")).strip() or None,
                   ciiu=cod_ciiu(ciiu_raw), sector=(str(get(r, "sector") or "")).strip() or sector_de(ciiu_raw),
                   depto=(str(get(r, "depto") or "")).strip() or None, ciudad=(str(get(r, "ciudad") or "")).strip() or None,
                   web=(str(get(r, "web") or "")).strip() or None, activos=activos, pasivos=num(get(r, "pasivos")),
                   patrimonio=num(get(r, "patrimonio")), utilidad_op=num(get(r, "utilidad_op")), prioridad=pr,
                   prioridad_auto=auto, notas=(str(get(r, "notas") or "")).strip() or None,
                   revisar="; ".join(flags) or None, origen=f.filename, creado=tcol)
        ct = {k: (str(get(r, "c_" + k) or "")).strip() or None for k in ("nombre", "cargo", "telefono", "correo")}
        registros.append((n, r, emp, ct, flags))
        marker = -len(registros)
        if nit:
            vistos_nit[nit] = marker
        vistos_norm.setdefault(rn, marker)
    resumen = {"archivo": f.filename, "filas": len(data) - 1, "nuevas": len(registros), "duplicadas": len(dups),
               "errores": len(errs), "para_revisar": sum(1 for x in registros if x[4]),
               "columnas_detectadas": {k: headers[i] for k, i in m.items()}}
    if preview:
        resumen["preview"] = True
        resumen["ejemplos_errores"] = [{"fila": e[0], "motivo": e[1]} for e in errs[:10]]
        return jsonify(resumen)
    # ---- escritura
    cur = db.execute("INSERT INTO importaciones(archivo,fecha,total,nuevas,duplicadas,errores,revisar) VALUES(?,?,?,?,?,?,?)",
                     (f.filename, tcol, resumen["filas"], resumen["nuevas"], resumen["duplicadas"], resumen["errores"], resumen["para_revisar"]))
    imp_id = cur.lastrowid
    ids = {}
    for idx, (n, r, emp, ct, flags) in enumerate(registros, start=1):
        c = db.execute(f"INSERT INTO empresas({','.join(emp)}) VALUES({','.join('?' * len(emp))})", list(emp.values()))
        ids[-idx] = c.lastrowid
        if any(ct.values()):
            db.execute("INSERT INTO contactos(empresa_id,nombre,cargo,telefono,correo,creado) VALUES(?,?,?,?,?,?)",
                       (c.lastrowid, ct["nombre"], ct["cargo"], ct["telefono"], ct["correo"], tcol))
        db.execute("INSERT INTO import_filas(importacion_id,fila,empresa_id,estado,motivo,datos) VALUES(?,?,?,?,?,?)",
                   (imp_id, n, c.lastrowid, "importada", "; ".join(flags) or None, json.dumps(dict(zip(headers, map(_ser, r))), ensure_ascii=False)))
    for n, motivo, r, target in dups:
        eid = target if target > 0 else ids.get(target)
        db.execute("INSERT INTO import_filas(importacion_id,fila,empresa_id,estado,motivo,datos) VALUES(?,?,?,?,?,?)",
                   (imp_id, n, eid, "duplicada", motivo, json.dumps(dict(zip(headers, map(_ser, r))), ensure_ascii=False)))
        if eid and "repetido en el archivo" in motivo:
            db.execute("UPDATE empresas SET filas_repetidas=filas_repetidas+1, revisar=COALESCE(revisar||'; ','')||'NIT repetido en archivo' WHERE id=?", (eid,))
    for n, motivo, r in errs:
        db.execute("INSERT INTO import_filas(importacion_id,fila,empresa_id,estado,motivo,datos) VALUES(?,?,?,?,?,?)",
                   (imp_id, n, None, "error", motivo, json.dumps(dict(zip(headers, map(_ser, r))), ensure_ascii=False)))
    db.commit()
    MB.clasificar_todo(db, solo_nulos=True)
    MB.puntuar_todo(db)
    resumen["importacion_id"] = imp_id
    return jsonify(resumen), 201


def _ser(v):
    return v if isinstance(v, (str, int, float, type(None))) else str(v)


@app.get("/api/importaciones")
def listar_importaciones():
    return jsonify(rows("SELECT id,archivo,fecha,total,nuevas,duplicadas,errores,revisar FROM importaciones ORDER BY id DESC"))


@app.get("/api/importaciones/<int:iid>/reporte.csv")
def reporte_importacion(iid):
    data = rows("SELECT fila,estado,motivo,empresa_id,datos FROM import_filas WHERE importacion_id=? AND (estado<>'importada' OR motivo IS NOT NULL) ORDER BY fila", (iid,))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["fila_archivo", "estado", "motivo", "empresa_id_crm", "datos_originales"])
    for r in data:
        w.writerow([r["fila"], r["estado"], r["motivo"] or "", r["empresa_id"] or "", r["datos"]])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=reporte_importacion_{iid}.csv"})


@app.get("/api/respaldo")
def respaldo():
    dst = os.path.join(DATA_DIR, "respaldo_tmp.db")
    if os.path.exists(dst):
        os.remove(dst)
    src = sqlite3.connect(DB_PATH)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    return send_file(dst, as_attachment=True, download_name=f"veltrix_crm_respaldo_{hoy()}.db")


# ---------------------------------------------------------------- estaticos
@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE, "static"), "index.html")


@app.get("/api/config")
def config():
    return jsonify({"etapas": ETAPAS, "activas": ACTIVAS, "calientes": sorted(CALIENTES), "tipos": TIPOS_INT, "hoy": hoy()})


for _m in (MS, MB, MD, MA, MT, MAG, MIA):
    _m.register(app, sys.modules[__name__])
init_db()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1") and not MS.auth_on():
        sys.exit("Por seguridad no se abre el CRM en red con la autenticación apagada (VELTRIX_AUTH=0).")
    if getattr(app, "_bg_pendiente", None):
        import threading
        threading.Thread(target=app._bg_pendiente, daemon=True).start()
    app.run(host=host, port=int(os.environ.get("PORT", 5000)), debug=False)
