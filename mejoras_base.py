"""Base: migración aditiva, auditoría, limpieza de calidad y puntuación comercial configurable.

Nada se borra ni se sobrescribe: solo se agregan columnas (calidad, score) y tablas nuevas.
"""
import bisect
import copy
import csv
import io
import json
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Response, jsonify, request, session

TZ = ZoneInfo("America/Bogota")
COLS_NUEVAS = [("calidad", "TEXT"), ("calidad_motivo", "TEXT"), ("calidad_manual", "INTEGER DEFAULT 0"),
               ("score", "REAL"), ("score_detalle", "TEXT")]
SCHEMA = """
CREATE TABLE IF NOT EXISTS auditoria(id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, entidad TEXT, entidad_id INTEGER,
  campo TEXT, antes TEXT, despues TEXT, motivo TEXT, usuario TEXT);
CREATE INDEX IF NOT EXISTS ix_aud ON auditoria(entidad, entidad_id);
CREATE TABLE IF NOT EXISTS config(clave TEXT PRIMARY KEY, valor TEXT);
"""

# Valores iniciales EDITABLES (Configuración). Sectores = prioridades declaradas por Veltrix (alimentos, manufactura,
# logística, salud, retail, energía, agroindustria, construcción) y tamaño mediano/grande; no son hechos del mercado.
SCORING_DEF = {
    "pesos": {"ubicacion": 25, "sector": 25, "tamano": 20, "datos": 15, "financiero": 10, "experiencia": 5},
    "ubicacion": {"ciudades": {"CALI": 1.0, "YUMBO": 0.9, "PALMIRA": 0.9, "JAMUNDI": 0.9},
                  "deptos": {"VALLE": 0.7}, "otros": 0.1},
    "sectores": {"10": 1.0, "11": 1.0, "13": 0.9, "14": 0.9, "15": 0.9, "16": 0.9, "17": 0.9, "18": 0.9, "19": 0.9, "20": 0.9, "21": 0.9, "22": 0.9, "23": 0.9, "24": 0.9, "25": 0.9, "26": 0.9, "27": 0.9, "28": 0.9, "29": 0.9, "30": 0.9, "31": 0.9, "32": 0.9, "33": 0.9, "49": 1.0, "52": 1.0, "86": 1.0, "47": 1.0, "46": 0.9, "35": 0.9, "01": 0.8, "03": 0.6, "41": 0.8, "42": 0.8, "43": 0.8, "56": 0.5, "62": 0.5, "70": 0.5, "85": 0.5, "64": 0.2, "65": 0.2, "66": 0.2, "68": 0.2},
    "sector_otros": 0.5,
    "casos_exito_ciiu": [],
    "tamano_pct": {"min": 50, "max": 99},
}
PROSPECCION_DEF = {"ambito": "valle", "solo_validos": False}
AMBITOS = {"cali": "e.ciudad LIKE 'CALI%'", "valle": "e.depto LIKE 'VALLE%'", "todo": "1=1"}

EXPLICACION = ("Puntaje 0-100 = suma ponderada de seis componentes (cada uno entre 0 y 1 × su peso): "
               "ubicación (Cali/Valle según la tabla), sector (división CIIU según la tabla), tamaño (percentil de activos "
               "dentro de la base: máximo entre los percentiles configurados, menos puntos fuera del rango), "
               "datos de contacto (teléfono, correo, nombre, web registrados), señales financieras simples (patrimonio y "
               "utilidad operacional positivos) y experiencia previa (CIIU de casos de éxito que usted declare). "
               "Límites: las cifras son de un solo corte anual y sin verificar la unidad; la base no trae teléfonos ni correos, "
               "así que 'datos' será 0 hasta que los registre; el puntaje NO es una probabilidad de cierre.")

GENERICAS = {"sas", "sa", "ltda", "s a s", "s a", "e u", "eu", "sociedad", "empresa", "sin nombre", "no registra",
             "na", "n a", "null", "none", "prueba", "test", "xxx", "nn", "sin informacion", "no aplica", "sucursal",
             "sucursal colombia", "sociedad por acciones simplificada"}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def _core(s):
    t = " " + _norm(s) + " "
    t = re.sub(r"\b(s a s|sas|s a|sa|ltda|limitada|e u|eu|s en c)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def ahora():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ migración
def migracion_pendiente(db):
    return "calidad" not in {r[1] for r in db.execute("PRAGMA table_info(empresas)")}


def migrate(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(empresas)")}
    for n, t in COLS_NUEVAS:
        if n not in cols:
            db.execute(f"ALTER TABLE empresas ADD COLUMN {n} {t}")
    db.executescript(SCHEMA)
    db.execute("CREATE INDEX IF NOT EXISTS ix_emp_calidad ON empresas(calidad)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_emp_score ON empresas(score)")


def cfg_get(db, clave, default):
    r = db.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
    base = copy.deepcopy(default)
    if r:
        try:
            v = json.loads(r[0])
            if isinstance(base, dict) and isinstance(v, dict):
                base.update(v)
                return base
            return v
        except ValueError:
            pass
    return base


def cfg_set(db, clave, valor):
    db.execute("INSERT OR REPLACE INTO config(clave,valor) VALUES(?,?)", (clave, json.dumps(valor, ensure_ascii=False)))


def audit(db, entidad, eid, campo, antes, despues, motivo=None):
    try:
        usuario = session.get("u")
    except RuntimeError:
        usuario = None
    db.execute("INSERT INTO auditoria(fecha,entidad,entidad_id,campo,antes,despues,motivo,usuario) VALUES(?,?,?,?,?,?,?,?)",
               (ahora(), entidad, eid, campo, None if antes is None else str(antes),
                None if despues is None else str(despues), motivo, usuario))


def audit_cambios(db, entidad, eid, old, nuevo, motivo=None):
    for k, v in nuevo.items():
        if k in old and str(old[k]) != str(v):
            audit(db, entidad, eid, k, old[k], v, motivo)


# ------------------------------------------------------------------ calidad
def evaluar(e):
    """Devuelve (estado, [motivos]). estado: valido | revisar | excluido."""
    ex, rv = [], []
    r = (e.get("razon") or "").strip()
    rn, core = _norm(r), _core(r)
    if not r:
        ex.append("Razón social vacía")
    elif rn in GENERICAS or not core or core in GENERICAS:
        ex.append("Razón social genérica (solo sufijo societario o texto sin nombre)")
    elif not re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]", r):
        ex.append("Razón social sin letras")
    elif len(re.sub(r"[^a-z]", "", rn)) < 3:
        rv.append("Razón social demasiado corta (verifique)")
    if r and re.match(r"^0[A-Za-zÁÉÍÓÚÑ]", r):
        rv.append("Razón social empieza con un 0 pegado (posible error de captura)")
    if re.search(r"\b(en liquidacion|en reorganizacion|liquidacion judicial|en concordato)\b", rn):
        rv.append("Posible empresa en liquidación/reorganización (riesgo de cobro)")
    nit = e.get("nit")
    if not nit:
        rv.append("Sin NIT")
    elif len(nit) < 6 or len(set(nit)) == 1:
        rv.append("NIT con formato sospechoso")
    if not e.get("dup_revisado"):
        if e.get("filas_repetidas"):
            rv.append("NIT repetido en el archivo importado")
        if e.get("n_sim"):
            rv.append("Razón social similar a otra empresa (posible duplicado)")
    if e.get("activos") is None or e["activos"] <= 0:
        rv.append("Sin activos reportados")
    if not (e.get("ciudad") or "").strip() or not (e.get("depto") or "").strip():
        rv.append("Sin ciudad o departamento")
    if ex:
        return "excluido", ex + rv
    return ("revisar", rv) if rv else ("valido", [])


def _ids_sql(ids):
    return f" AND id IN ({','.join(str(int(i)) for i in ids)})" if ids else ""


def clasificar_todo(db, solo_nulos=False, ids=None):
    """Clasifica. Respeta decisiones manuales (calidad_manual=1). No elimina nada."""
    cond = ("calidad IS NULL AND COALESCE(calidad_manual,0)=0" if solo_nulos else "COALESCE(calidad_manual,0)=0") + _ids_sql(ids)
    cur = db.execute(f"""SELECT id,nit,razon,ciudad,depto,activos,filas_repetidas,dup_revisado,calidad,
        (SELECT COUNT(*) FROM empresas x WHERE x.razon_norm=e.razon_norm AND x.id<>e.id AND e.razon_norm<>'') n_sim
        FROM empresas e WHERE {cond}""")
    cols = [c[0] for c in cur.description]
    upd, cambios = [], 0
    conteo = {"valido": 0, "revisar": 0, "excluido": 0}
    for row in cur.fetchall():
        e = dict(zip(cols, row))
        est, mot = evaluar(e)
        conteo[est] += 1
        upd.append((est, "; ".join(mot) or None, e["id"]))
        if est == "excluido" or (e["calidad"] and e["calidad"] != est):
            audit(db, "empresa", e["id"], "calidad", e["calidad"], est, "; ".join(mot) or "clasificación automática")
            cambios += 1
    db.executemany("UPDATE empresas SET calidad=?, calidad_motivo=? WHERE id=?", upd)
    audit(db, "empresas", 0, "clasificacion_lote", None, json.dumps(conteo), f"{len(upd)} registros evaluados")
    db.commit()
    return {"evaluadas": len(upd), **conteo}


# ------------------------------------------------------------------ puntuación
def _div(ciiu):
    m = re.match(r"^[A-Za-z]?(\d{2})", ciiu or "")
    return m.group(1) if m else None


def componentes(e, cfg, pct, c):
    pesos = cfg["pesos"]
    out = {}
    u = cfg["ubicacion"]
    ciudad = (e["ciudad"] or "").split("-")[0].strip().upper()
    depto = (e["depto"] or "").strip().upper()
    if ciudad in u["ciudades"]:
        out["ubicacion"] = (u["ciudades"][ciudad], ciudad.title())
    elif depto in u["deptos"]:
        out["ubicacion"] = (u["deptos"][depto], depto.title())
    else:
        out["ubicacion"] = (u["otros"], "Fuera de zona prioritaria")
    d = _div(e["ciiu"])
    out["sector"] = (cfg["sectores"].get(d, cfg["sector_otros"]) if d else cfg["sector_otros"], f"CIIU {d or 's/d'}")
    a = e["activos"]
    if a and a > 0:
        p = pct(a)
        lo, hi = cfg["tamano_pct"]["min"], cfg["tamano_pct"]["max"]
        v = p / lo if p < lo else (1.0 if p <= hi else 0.7)
        out["tamano"] = (min(v, 1.0), f"percentil {p:.0f}")
    else:
        out["tamano"] = (0.0, "sin activos")
    cs = c or (0, 0, 0)
    f = 0.4 * cs[0] + 0.3 * cs[1] + 0.2 * cs[2] + 0.1 * (1 if (e["web"] or "").strip() else 0)
    out["datos"] = (f, "tel/correo/nombre/web" if f else "sin datos de contacto")
    fin = (0.5 if (e["patrimonio"] or 0) > 0 else 0) + (0.5 if (e["utilidad_op"] or 0) > 0 else 0)
    out["financiero"] = (fin, "patrimonio y utilidad op." if fin else "sin señales positivas")
    cod = re.sub(r"^[A-Za-z]", "", e["ciiu"] or "")
    ok = any(cod.startswith(str(p)) for p in cfg["casos_exito_ciiu"])
    out["experiencia"] = (1.0 if ok else 0.0, "caso de éxito en el sector" if ok else "sin caso declarado")
    tot = sum(pesos.values()) or 1
    detalle = {k: [round(v * pesos[k] / tot * 100, 1), round(pesos[k] / tot * 100, 1), txt] for k, (v, txt) in out.items()}
    return round(sum(x[0] for x in detalle.values()), 1), detalle


def puntuar_todo(db, solo_nulos=False, ids=None):
    cfg = cfg_get(db, "scoring", SCORING_DEF)
    acts = sorted(r[0] for r in db.execute("SELECT activos FROM empresas WHERE activos>0"))
    n = len(acts) or 1
    pct = lambda a: bisect.bisect_left(acts, a) / n * 100
    contactos = {r[0]: (r[1], r[2], r[3]) for r in db.execute(
        """SELECT empresa_id, MAX(COALESCE(telefono,'')<>''), MAX(COALESCE(correo,'')<>''), MAX(COALESCE(nombre,'')<>'')
           FROM contactos GROUP BY empresa_id""")}
    cur = db.execute(f"""SELECT id,ciudad,depto,ciiu,activos,patrimonio,utilidad_op,web,calidad FROM empresas
        WHERE 1=1 {'AND score IS NULL' if solo_nulos else ''}{_ids_sql(ids)}""")
    cols = [c[0] for c in cur.description]
    upd = []
    for row in cur.fetchall():
        e = dict(zip(cols, row))
        if e["calidad"] == "excluido":
            upd.append((0.0, json.dumps({"nota": "Excluida por calidad de datos"}, ensure_ascii=False), e["id"]))
            continue
        s, det = componentes(e, cfg, pct, contactos.get(e["id"]))
        upd.append((s, json.dumps(det, ensure_ascii=False), e["id"]))
    db.executemany("UPDATE empresas SET score=?, score_detalle=? WHERE id=?", upd)
    db.commit()
    return len(upd)


def prospectar(A, limite, ambito=None):
    """Empresas sin trabajar ordenadas por puntaje, dentro del ámbito elegido y sin excluidas."""
    cfg = cfg_get(A.get_db(), "prospeccion", PROSPECCION_DEF)
    amb = AMBITOS.get(ambito or cfg["ambito"], AMBITOS["valle"])
    cal = "e.calidad='valido'" if cfg.get("solo_validos") else "COALESCE(e.calidad,'valido')<>'excluido'"
    return A.rows(f"""{A.BASE_SELECT} WHERE NOT EXISTS(SELECT 1 FROM oportunidades o WHERE o.empresa_id=e.id)
        AND NOT EXISTS(SELECT 1 FROM interacciones i WHERE i.empresa_id=e.id) AND {cal} AND {amb}
        ORDER BY e.score DESC, e.activos DESC LIMIT ?""", (limite,))


# ------------------------------------------------------------------ API
def register(app, A):
    @app.get("/api/calidad/resumen")
    def cal_resumen():
        r = A.rows("SELECT COALESCE(calidad,'sin_clasificar') estado, COUNT(*) n FROM empresas GROUP BY 1")
        mot = {}
        for x in A.rows("SELECT calidad_motivo FROM empresas WHERE calidad_motivo IS NOT NULL"):
            for m in x["calidad_motivo"].split("; "):
                mot[m] = mot.get(m, 0) + 1
        return jsonify({"estados": {x["estado"]: x["n"] for x in r}, "motivos": sorted(mot.items(), key=lambda t: -t[1]),
                        "manuales": A.one("SELECT COUNT(*) n FROM empresas WHERE calidad_manual=1")["n"]})

    @app.post("/api/calidad/clasificar")
    def cal_clasificar():
        res = clasificar_todo(A.get_db())
        puntuar_todo(A.get_db())
        return jsonify(res)

    @app.get("/api/calidad/lista")
    def cal_lista():
        a = request.args
        est = a.get("estado", "excluido")
        per, page = 50, max(int(a.get("page", 1)), 1)
        w, p = ["calidad=?"], [est]
        if a.get("motivo"):
            w.append("calidad_motivo LIKE ?")
            p.append(f"%{a['motivo']}%")
        where = " AND ".join(w)
        tot = A.one(f"SELECT COUNT(*) n FROM empresas WHERE {where}", p)["n"]
        it = A.rows(f"""SELECT id,nit,razon,ciudad,depto,activos,calidad,calidad_motivo,calidad_manual FROM empresas
            WHERE {where} ORDER BY razon_norm LIMIT ? OFFSET ?""", p + [per, (page - 1) * per])
        return jsonify({"total": tot, "page": page, "per": per, "items": it})

    @app.get("/api/calidad/exportar.csv")
    def cal_exportar():
        est = request.args.get("estado", "excluido")
        data = A.rows("""SELECT id,nit,razon,tipo,ciiu,depto,ciudad,activos,pasivos,patrimonio,utilidad_op,calidad,calidad_motivo,
            calidad_manual FROM empresas WHERE calidad=? ORDER BY razon_norm""", (est,))
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "nit", "razon", "tipo", "ciiu", "depto", "ciudad", "activos", "pasivos", "patrimonio", "utilidad_op",
                    "estado", "motivo", "decision_manual"])
        for r in data:
            w.writerow(["" if v is None else v for v in r.values()])
        return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=depuracion_{est}_{A.hoy()}.csv"})

    @app.post("/api/empresas/<int:eid>/calidad")
    def cal_manual(eid):
        d = A.body()
        est = d.get("estado")
        e = A.one("SELECT id,calidad FROM empresas WHERE id=?", (eid,))
        if not e:
            raise A.ApiError("Empresa no encontrada.", 404)
        db = A.get_db()
        if est == "auto":
            db.execute("UPDATE empresas SET calidad_manual=0 WHERE id=?", (eid,))
            db.commit()
            clasificar_todo(db, ids=[eid])
            puntuar_todo(db, ids=[eid])
            return jsonify({"ok": True})
        if est not in ("valido", "revisar", "excluido"):
            raise A.ApiError("Estado no válido.")
        mot = (d.get("motivo") or "").strip() or ("Excluida manualmente" if est == "excluido" else "Restaurada manualmente")
        db.execute("UPDATE empresas SET calidad=?, calidad_motivo=?, calidad_manual=1 WHERE id=?", (est, mot, eid))
        audit(db, "empresa", eid, "calidad", e["calidad"], est, mot)
        db.commit()
        puntuar_todo(db, ids=[eid])
        return jsonify({"ok": True})

    @app.get("/api/auditoria")
    def api_auditoria():
        a = request.args
        w, p = [], []
        if a.get("entidad"):
            w.append("entidad=?")
            p.append(a["entidad"])
        if a.get("id"):
            w.append("entidad_id=?")
            p.append(int(a["id"]))
        return jsonify(A.rows(f"SELECT * FROM auditoria {'WHERE ' + ' AND '.join(w) if w else ''} ORDER BY id DESC LIMIT 200", p))

    @app.get("/api/config/scoring")
    def get_scoring():
        return jsonify({"config": cfg_get(A.get_db(), "scoring", SCORING_DEF), "explicacion": EXPLICACION})

    @app.put("/api/config/scoring")
    def put_scoring():
        d = A.body()
        cfg = cfg_get(A.get_db(), "scoring", SCORING_DEF)
        try:
            if "pesos" in d:
                pesos = {k: float(v) for k, v in d["pesos"].items() if k in cfg["pesos"]}
                merged = {**cfg["pesos"], **pesos}
                if any(v < 0 for v in merged.values()) or sum(merged.values()) <= 0:
                    raise ValueError
                cfg["pesos"] = merged
            for k in ("ubicacion", "sectores"):
                if k in d:
                    cfg[k] = d[k]
            if "casos_exito_ciiu" in d:
                cfg["casos_exito_ciiu"] = [str(x).strip() for x in d["casos_exito_ciiu"] if str(x).strip()]
            if "tamano_pct" in d:
                cfg["tamano_pct"] = {"min": float(d["tamano_pct"]["min"]), "max": float(d["tamano_pct"]["max"])}
        except (ValueError, TypeError, KeyError):
            raise A.ApiError("Configuración de puntaje no válida (pesos numéricos ≥ 0 y suma > 0).")
        cfg_set(A.get_db(), "scoring", cfg)
        audit(A.get_db(), "config", 0, "scoring", None, "actualizado")
        A.get_db().commit()
        return jsonify({"ok": True, "recalculadas": puntuar_todo(A.get_db())})

    @app.post("/api/scoring/recalcular")
    def recalcular():
        return jsonify({"recalculadas": puntuar_todo(A.get_db())})

    @app.get("/api/config/prospeccion")
    def get_prosp():
        return jsonify(cfg_get(A.get_db(), "prospeccion", PROSPECCION_DEF))

    @app.put("/api/config/prospeccion")
    def put_prosp():
        d = A.body()
        cfg = cfg_get(A.get_db(), "prospeccion", PROSPECCION_DEF)
        if "ambito" in d:
            if d["ambito"] not in AMBITOS:
                raise A.ApiError("Ámbito no válido (cali, valle o todo).")
            cfg["ambito"] = d["ambito"]
        if "solo_validos" in d:
            cfg["solo_validos"] = bool(d["solo_validos"])
        cfg_set(A.get_db(), "prospeccion", cfg)
        A.get_db().commit()
        return jsonify(cfg)

    @app.get("/api/prospeccion")
    def api_prospeccion():
        return jsonify(prospectar(A, min(int(request.args.get("limite", 20)), 200), request.args.get("ambito")))
