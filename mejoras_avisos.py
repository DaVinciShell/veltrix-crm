"""Recordatorios y tareas, centro de notificaciones, mensajes preparados (WhatsApp / correo) y resumen diario.

Alcance real:
- Centro de notificaciones y avisos del navegador: funcionan MIENTRAS el CRM está abierto en el navegador.
- Resumen por correo: requiere configurar SMTP por variables de entorno y que algo ejecute el envío
  (el servidor encendido, o la Tarea Programada de Windows con resumen_diario.py). Con todo apagado no hay avisos.
- WhatsApp/correo: se PREPARA el mensaje y se abre WhatsApp / el cliente de correo. No hay envío automático ni masivo.
"""
import os
import re
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import quote

from flask import jsonify, request

import mejoras_base as MB
import mejoras_seguridad as MS

TIPOS_TAREA = ["Llamada", "Correo", "Reunión", "Propuesta", "Cobro", "Otro"]
FRECUENCIAS = ["una_vez", "diaria", "semanal", "mensual"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS tareas(id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, titulo TEXT NOT NULL, empresa_id INTEGER,
  oportunidad_id INTEGER, documento_id INTEGER, fecha TEXT NOT NULL, hora TEXT, frecuencia TEXT DEFAULT 'una_vez',
  estado TEXT DEFAULT 'pendiente', notificar_correo INTEGER DEFAULT 0, creado TEXT, completada TEXT);
CREATE INDEX IF NOT EXISTS ix_tarea ON tareas(estado, fecha);
CREATE TABLE IF NOT EXISTS plantillas(id INTEGER PRIMARY KEY AUTOINCREMENT, clave TEXT, canal TEXT, nombre TEXT, asunto TEXT, cuerpo TEXT,
  UNIQUE(clave, canal));
"""
PLANTILLAS_DEF = [
    ("primer_contacto", "whatsapp", "Primer contacto", "",
     "{saludo} soy {mi_nombre}, de {mi_empresa}. Acompañamos a empresas a ordenar su desempeño y su gestión. "
     "Me gustaría conversar 15 minutos con {empresa} sobre cómo están organizando hoy su gestión comercial y de talento. "
     "¿Tiene un espacio esta semana?"),
    ("primer_contacto", "correo", "Primer contacto", "Conversación breve con {empresa}",
     "{saludo}\n\nSoy {mi_nombre}, de {mi_empresa}. Acompañamos a empresas a ordenar su desempeño organizacional y su gestión.\n\n"
     "Quisiera proponerle una conversación de 15 minutos para entender cómo está trabajando {empresa} estos frentes y ver si podemos aportarle.\n\n"
     "¿Qué día de esta semana le queda bien?\n\nCordial saludo,\n{mi_nombre}\n{mi_empresa}"),
    ("seguimiento", "whatsapp", "Seguimiento", "",
     "{saludo} le escribo para retomar nuestra conversación con {empresa}. ¿Pudo revisar lo que hablamos? Quedo atento para definir el siguiente paso."),
    ("seguimiento", "correo", "Seguimiento", "Seguimiento · {empresa}",
     "{saludo}\n\nRetomo nuestra conversación con {empresa}. Quedo atento a sus comentarios para definir el siguiente paso.\n\nCordial saludo,\n{mi_nombre}\n{mi_empresa}"),
    ("envio_propuesta", "whatsapp", "Envío de propuesta", "",
     "{saludo} le comparto la propuesta {propuesta} para {empresa}. Tiene vigencia hasta el {vigencia}. ¿Agendamos 15 minutos para resolver dudas?"),
    ("envio_propuesta", "correo", "Envío de propuesta", "Propuesta {propuesta} · {empresa}",
     "{saludo}\n\nAdjunto la propuesta {propuesta} para {empresa}, con vigencia hasta el {vigencia}.\n\nQuedo atento para resolver cualquier duda.\n\nCordial saludo,\n{mi_nombre}\n{mi_empresa}"),
    ("recuperacion", "whatsapp", "Recuperar oportunidad", "",
     "{saludo} hace un tiempo conversamos con {empresa} y quedamos en retomar. ¿Sigue siendo un tema relevante para ustedes? Con gusto lo revisamos."),
    ("recuperacion", "correo", "Recuperar oportunidad", "¿Retomamos? · {empresa}",
     "{saludo}\n\nHace un tiempo conversamos con {empresa} y no alcanzamos a concretar. ¿Sigue siendo un tema relevante? Con gusto lo revisamos.\n\nCordial saludo,\n{mi_nombre}\n{mi_empresa}"),
    ("recordatorio_cobro", "whatsapp", "Recordatorio de cobro", "",
     "{saludo} le recuerdo la cuenta de cobro {cuenta_cobro} de {empresa} por {saldo}, con fecha límite {vigencia}. Quedo atento a la confirmación del pago. Gracias."),
    ("recordatorio_cobro", "correo", "Recordatorio de cobro", "Cuenta de cobro {cuenta_cobro} · {empresa}",
     "{saludo}\n\nLe recuerdo la cuenta de cobro {cuenta_cobro} por {saldo}, con fecha límite {vigencia}. Agradezco confirmar el pago.\n\nCordial saludo,\n{mi_nombre}\n{mi_empresa}"),
]


def migrate(db):
    db.executescript(SCHEMA)
    for p in PLANTILLAS_DEF:
        db.execute("INSERT OR IGNORE INTO plantillas(clave,canal,nombre,asunto,cuerpo) VALUES(?,?,?,?,?)", p)


def telefono_whatsapp(t):
    """Normaliza a formato internacional colombiano. Devuelve None si no parece un celular válido (no inventa números)."""
    d = re.sub(r"\D", "", t or "")
    if len(d) == 10 and d.startswith("3"):
        return "57" + d
    if len(d) == 12 and d.startswith("573"):
        return d
    return None


def _sumar_mes(f):
    d = datetime.strptime(f, "%Y-%m-%d")
    m, y = d.month % 12 + 1, d.year + (1 if d.month == 12 else 0)
    for dia in (d.day, 30, 29, 28):
        try:
            return d.replace(year=y, month=m, day=dia).strftime("%Y-%m-%d")
        except ValueError:
            continue


def siguiente_fecha(f, frec):
    d = datetime.strptime(f, "%Y-%m-%d")
    if frec == "diaria":
        return (d + timedelta(days=1)).strftime("%Y-%m-%d")
    if frec == "semanal":
        return (d + timedelta(days=7)).strftime("%Y-%m-%d")
    if frec == "mensual":
        return _sumar_mes(f)
    return None


# ------------------------------------------------------------------ correo (resumen diario)
def smtp_config():
    c = {k: os.environ.get("VELTRIX_SMTP_" + k.upper()) for k in ("host", "port", "user", "pass", "from", "to")}
    return c if c["host"] and c["user"] and c["pass"] and c["to"] else None


def enviar_correo(asunto, cuerpo, conf=None):
    c = conf or smtp_config()
    if not c:
        raise RuntimeError("SMTP no configurado (variables VELTRIX_SMTP_HOST, _PORT, _USER, _PASS, _FROM, _TO).")
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = asunto, c.get("from") or c["user"], c["to"]
    m.set_content(cuerpo)
    with smtplib.SMTP(c["host"], int(c.get("port") or 587), timeout=30) as s:
        s.starttls()
        s.login(c["user"], c["pass"])
        s.send_message(m)


def register(app, A):
    def notificaciones():
        t = A.hoy()
        prox = (datetime.now(A.TZ) + timedelta(days=7)).strftime("%Y-%m-%d")
        items = []

        def add(nivel, tipo, titulo, detalle, fecha, link, hora=None, key=None):
            items.append({"nivel": nivel, "tipo": tipo, "titulo": titulo, "detalle": detalle, "fecha": fecha, "hora": hora, "link": link, "key": key})

        nivel = lambda f: "vencido" if f < t else "hoy" if f == t else "proximo"
        for o in A.rows(f"""SELECT o.id,o.empresa_id,o.etapa,o.proximo_paso,o.proxima_fecha,e.razon FROM oportunidades o JOIN empresas e ON e.id=o.empresa_id
                WHERE o.etapa IN ({','.join('?' * len(A.ACTIVAS))}) AND o.proxima_fecha<=? ORDER BY o.proxima_fecha""", list(A.ACTIVAS) + [prox]):
            add(nivel(o["proxima_fecha"]), "Seguimiento", o["razon"], f"{o['proximo_paso'] or 'Definir próximo paso'} ({o['etapa']})", o["proxima_fecha"],
                f"#empresa/{o['empresa_id']}", key=f"opp{o['id']}-{o['proxima_fecha']}")
        for k in A.rows("""SELECT k.*, e.razon FROM tareas k LEFT JOIN empresas e ON e.id=k.empresa_id WHERE k.estado='pendiente' AND k.fecha<=? ORDER BY k.fecha, k.hora""", (prox,)):
            add(nivel(k["fecha"]), k["tipo"] or "Tarea", k["titulo"], k["razon"] or "", k["fecha"], f"#empresa/{k['empresa_id']}" if k["empresa_id"] else "#tareas",
                hora=k["hora"], key=f"tarea{k['id']}-{k['fecha']}")
        dias_prop = int(MB.cfg_get(A.get_db(), "avisos", {"dias_propuesta": 5})["dias_propuesta"])
        lim = (datetime.now(A.TZ) - timedelta(days=dias_prop)).strftime("%Y-%m-%d")
        for d in A.rows("""SELECT d.*, e.razon FROM documentos d JOIN empresas e ON e.id=d.empresa_id
                WHERE d.tipo='propuesta' AND d.estado='enviado' AND d.fecha_envio<=?""", (lim,)):
            add("vencido", "Propuesta sin respuesta", d["razon"], f"{d['numero']} enviada el {d['fecha_envio']}", d["fecha_envio"], f"#empresa/{d['empresa_id']}", key=f"prop{d['id']}")
        for d in A.rows("""SELECT d.*, e.razon FROM documentos d JOIN empresas e ON e.id=d.empresa_id
                WHERE d.tipo='cuenta_cobro' AND d.estado IN ('generado','enviado') AND d.fecha_vigencia<=?""", ((datetime.now(A.TZ) + timedelta(days=3)).strftime("%Y-%m-%d"),)):
            ven = d["fecha_vigencia"] < t
            add("vencido" if ven else "hoy" if d["fecha_vigencia"] == t else "proximo", "Cobro vencido" if ven else "Cobro por vencer", d["razon"],
                f"{d['numero']} · saldo ${round(d['valor'] - A.one('SELECT COALESCE(SUM(monto),0) n FROM cobros WHERE documento_id=?', (d['id'],))['n']):,}".replace(",", "."),
                d["fecha_vigencia"], f"#empresa/{d['empresa_id']}", key=f"cc{d['id']}")
        orden = {"vencido": 0, "hoy": 1, "proximo": 2}
        items.sort(key=lambda x: (orden[x["nivel"]], x["fecha"], x["hora"] or ""))
        cnt = {n: sum(1 for i in items if i["nivel"] == n) for n in orden}
        return {"fecha": t, "conteo": cnt, "items": items}

    A.notificaciones = notificaciones

    def resumen_texto():
        n = notificaciones()
        L = [f"Resumen VELTRIX · {n['fecha']}", f"Vencidos: {n['conteo']['vencido']} · Hoy: {n['conteo']['hoy']} · Próximos 7 días: {n['conteo']['proximo']}", ""]
        for nv, tit in (("vencido", "VENCIDOS"), ("hoy", "HOY"), ("proximo", "PRÓXIMOS")):
            xs = [i for i in n["items"] if i["nivel"] == nv]
            if xs:
                L.append(tit)
                L += [f"- {i['fecha']} {i['hora'] or ''} [{i['tipo']}] {i['titulo']} — {i['detalle']}" for i in xs[:40]]
                L.append("")
        return "\n".join(L)

    def enviar_resumen():
        cfg = MB.cfg_get(A.get_db(), "avisos", {})
        enviar_correo(f"VELTRIX · resumen del {A.hoy()}", resumen_texto())
        MB.cfg_set(A.get_db(), "ultimo_resumen", A.hoy())
        A.get_db().commit()
        return True

    A.resumen_texto, A.enviar_resumen = resumen_texto, enviar_resumen

    @app.get("/api/notificaciones")
    def api_notif():
        return jsonify(notificaciones())

    @app.get("/api/avisos/estado")
    def avisos_estado():
        c = smtp_config()
        return jsonify({"correo_configurado": bool(c), "destino": c["to"] if c else None,
                        "ultimo_resumen": MB.cfg_get(A.get_db(), "ultimo_resumen", None),
                        "nota": "Los avisos del navegador solo suenan con el CRM abierto. El correo diario requiere SMTP y que el servidor "
                                "(o la Tarea Programada de Windows con resumen_diario.py) esté ejecutándose."})

    @app.post("/api/avisos/probar-correo")
    def probar_correo():
        try:
            enviar_resumen()
        except Exception as e:  # noqa: BLE001
            raise A.ApiError(f"No se pudo enviar: {e}", 400)
        return jsonify({"ok": True})

    # ---- tareas
    def _tarea(d, parcial=False):
        out = {}
        if "titulo" in d or not parcial:
            titulo = d.get("titulo")
            if not isinstance(titulo, str) or not titulo.strip():
                raise A.ApiError("Escriba el título de la tarea.")
            out["titulo"] = titulo.strip()
        if "fecha" in d or not parcial:
            if not A.valid_date(d.get("fecha")):
                raise A.ApiError("Fecha no válida (AAAA-MM-DD).")
            out["fecha"] = d["fecha"]
        if d.get("hora") not in (None, ""):
            if not isinstance(d["hora"], str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", d["hora"]):
                raise A.ApiError("Hora no válida (HH:MM).")
            out["hora"] = d["hora"]
        elif "hora" in d:
            out["hora"] = None
        if "tipo" in d:
            if not isinstance(d["tipo"], str):
                raise A.ApiError("Tipo de tarea no válido.")
            out["tipo"] = d["tipo"] if d["tipo"] in TIPOS_TAREA else "Otro"
        if "frecuencia" in d:
            if not isinstance(d["frecuencia"], str) or d["frecuencia"] not in FRECUENCIAS:
                raise A.ApiError("Frecuencia no válida.")
            out["frecuencia"] = d["frecuencia"]
        lookup = {
            "empresa_id": ("empresas", "Empresa"),
            "oportunidad_id": ("oportunidades", "Oportunidad"),
            "documento_id": ("documentos", "Documento"),
        }
        for k in ("empresa_id", "oportunidad_id", "documento_id"):
            if k in d and d[k] not in (None, ""):
                if isinstance(d[k], bool) or not isinstance(d[k], (str, int)):
                    raise A.ApiError(f"{lookup[k][1]} no válido.")
                try:
                    value = int(d[k])
                except (TypeError, ValueError):
                    raise A.ApiError(f"{lookup[k][1]} no válido.")
                if value <= 0:
                    raise A.ApiError(f"{lookup[k][1]} no válido.")
                table, label = lookup[k]
                if not A.one(f"SELECT id FROM {table} WHERE id=?", (value,)):
                    raise A.ApiError(f"{label} no encontrada.", 404)
                out[k] = value
        empresa_id = out.get("empresa_id")
        oportunidad_id = out.get("oportunidad_id")
        documento_id = out.get("documento_id")
        if empresa_id and oportunidad_id and A.one("SELECT id FROM oportunidades WHERE id=? AND empresa_id=?", (oportunidad_id, empresa_id)) is None:
            raise A.ApiError("La oportunidad no corresponde a la empresa indicada.")
        if documento_id:
            documento = A.one("SELECT empresa_id,oportunidad_id FROM documentos WHERE id=?", (documento_id,))
            if empresa_id and documento["empresa_id"] != empresa_id:
                raise A.ApiError("El documento no corresponde a la empresa indicada.")
            if oportunidad_id and documento["oportunidad_id"] != oportunidad_id:
                raise A.ApiError("El documento no corresponde a la oportunidad indicada.")
        if "notificar_correo" in d:
            if not isinstance(d["notificar_correo"], bool):
                raise A.ApiError("La opción de notificación por correo debe ser booleana.")
            out["notificar_correo"] = 1 if d["notificar_correo"] else 0
        return out

    @app.get("/api/tareas")
    def listar_tareas():
        est = request.args.get("estado", "pendiente")
        w, p = ["k.estado=?"], [est]
        if request.args.get("empresa_id"):
            try:
                empresa_id = int(request.args["empresa_id"])
            except ValueError:
                raise A.ApiError("empresa_id debe ser un entero.")
            if empresa_id <= 0:
                raise A.ApiError("empresa_id debe ser positivo.")
            w.append("k.empresa_id=?")
            p.append(empresa_id)
        return jsonify(A.rows(f"""SELECT k.*, e.razon FROM tareas k LEFT JOIN empresas e ON e.id=k.empresa_id WHERE {' AND '.join(w)}
            ORDER BY k.fecha, k.hora LIMIT 300""", p))

    @app.post("/api/tareas")
    def crear_tarea():
        d = _tarea(A.body())
        d.setdefault("tipo", "Otro")
        d["creado"] = A.ahora()
        cur = A.get_db().execute(f"INSERT INTO tareas({','.join(d)}) VALUES({','.join('?' * len(d))})", list(d.values()))
        A.get_db().commit()
        return jsonify({"id": cur.lastrowid}), 201

    @app.put("/api/tareas/<int:tid>")
    def editar_tarea(tid):
        if not A.one("SELECT id FROM tareas WHERE id=?", (tid,)):
            raise A.ApiError("Tarea no encontrada.", 404)
        d = _tarea(A.body(), True)
        if d:
            A.get_db().execute(f"UPDATE tareas SET {','.join(k + '=?' for k in d)} WHERE id=?", list(d.values()) + [tid])
            A.get_db().commit()
        return jsonify({"ok": True})

    @app.post("/api/tareas/<int:tid>/hecha")
    def tarea_hecha(tid):
        k = A.one("SELECT * FROM tareas WHERE id=? AND estado='pendiente'", (tid,))
        if not k:
            raise A.ApiError("Tarea no encontrada o ya cerrada.", 404)
        db = A.get_db()
        db.execute("UPDATE tareas SET estado='hecha', completada=? WHERE id=?", (A.ahora(), tid))
        nueva = None
        sig = siguiente_fecha(k["fecha"], k["frecuencia"])
        while sig and sig <= A.hoy():
            sig = siguiente_fecha(sig, k["frecuencia"])
        if sig:
            cur = db.execute("""INSERT INTO tareas(tipo,titulo,empresa_id,oportunidad_id,documento_id,fecha,hora,frecuencia,notificar_correo,creado)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (k["tipo"], k["titulo"], k["empresa_id"], k["oportunidad_id"], k["documento_id"], sig, k["hora"], k["frecuencia"], k["notificar_correo"], A.ahora()))
            nueva = cur.lastrowid
        db.commit()
        return jsonify({"ok": True, "siguiente": nueva, "fecha_siguiente": sig})

    @app.delete("/api/tareas/<int:tid>")
    def cancelar_tarea(tid):
        if not A.one("SELECT id FROM tareas WHERE id=?", (tid,)):
            raise A.ApiError("Tarea no encontrada.", 404)
        A.get_db().execute("UPDATE tareas SET estado='cancelada' WHERE id=?", (tid,))
        A.get_db().commit()
        return jsonify({"ok": True})

    # ---- plantillas y mensajes
    @app.get("/api/plantillas")
    def listar_plantillas():
        return jsonify(A.rows("SELECT * FROM plantillas ORDER BY clave, canal"))

    @app.put("/api/plantillas/<int:pid>")
    def editar_plantilla(pid):
        d = A.body()
        if not (d.get("cuerpo") or "").strip():
            raise A.ApiError("El cuerpo no puede estar vacío.")
        A.get_db().execute("UPDATE plantillas SET asunto=?, cuerpo=? WHERE id=?", (d.get("asunto") or "", d["cuerpo"], pid))
        A.get_db().commit()
        return jsonify({"ok": True})

    @app.get("/api/mensajes/preparar")
    def preparar_mensaje():
        a = request.args
        e = A.one("SELECT * FROM empresas WHERE id=?", (a.get("empresa_id"),))
        if not e:
            raise A.ApiError("Empresa no encontrada.", 404)
        canal = a.get("canal", "whatsapp")
        pl = A.one("SELECT * FROM plantillas WHERE clave=? AND canal=?", (a.get("plantilla", "primer_contacto"), canal))
        if not pl:
            raise A.ApiError("Plantilla no encontrada.", 404)
        cs = A.rows("SELECT * FROM contactos WHERE empresa_id=? ORDER BY id", (e["id"],))
        c = next((x for x in cs if str(x["id"]) == a.get("contacto_id")), None) or (cs[0] if cs else None)
        em = MB.cfg_get(A.get_db(), "emisor", {})
        doc = A.one("SELECT * FROM documentos WHERE id=?", (a.get("documento_id"),)) if a.get("documento_id") else None
        if not doc and a.get("plantilla") == "envio_propuesta":
            doc = A.one("SELECT * FROM documentos WHERE empresa_id=? AND tipo='propuesta' AND estado<>'anulado' ORDER BY id DESC", (e["id"],))
        if not doc and a.get("plantilla") == "recordatorio_cobro":
            doc = A.one("SELECT * FROM documentos WHERE empresa_id=? AND tipo='cuenta_cobro' AND estado IN ('generado','enviado') ORDER BY id DESC", (e["id"],))
        nombre = (c["nombre"] or "").split()[0] if c and c["nombre"] else ""
        saldo = ""
        if doc:
            pag = A.one("SELECT COALESCE(SUM(monto),0) n FROM cobros WHERE documento_id=?", (doc["id"],))["n"]
            saldo = "$" + f"{round((doc['valor'] or 0) - pag):,}".replace(",", ".")
        vars_ = {"saludo": f"Hola {nombre}," if nombre else "Hola,", "empresa": e["razon"], "sector": e["sector"] or "", "ciudad": (e["ciudad"] or "").split("-")[0].title(),
                 "mi_nombre": (em.get("nombre") or "").strip() or "[su nombre]", "mi_empresa": em.get("nombre_comercial") or "VELTRIX",
                 "propuesta": doc["numero"] if doc and doc["tipo"] == "propuesta" else "[propuesta]", "vigencia": doc["fecha_vigencia"] if doc and doc["fecha_vigencia"] else "[fecha]",
                 "cuenta_cobro": doc["numero"] if doc and doc["tipo"] == "cuenta_cobro" else "[cuenta de cobro]", "saldo": saldo or "[valor]", "valor": saldo}
        fmt = lambda s: re.sub(r"\{(\w+)\}", lambda m: str(vars_.get(m.group(1), m.group(0))), s or "")
        cuerpo, asunto = fmt(pl["cuerpo"]), fmt(pl["asunto"])
        tel = telefono_whatsapp(c["telefono"]) if c else None
        correo = (c["correo"] or "").strip() if c else ""
        res = {"canal": canal, "cuerpo": cuerpo, "asunto": asunto, "contacto": c["nombre"] if c else None,
               "telefono": c["telefono"] if c else None, "correo": correo or None,
               "pendientes": sorted(set(re.findall(r"\[[^\]]+\]", cuerpo + asunto))),
               "wa_url": f"https://wa.me/{tel}?text={quote(cuerpo)}" if tel and canal == "whatsapp" else None,
               "mailto_url": f"mailto:{quote(correo)}?subject={quote(asunto)}&body={quote(cuerpo)}" if correo and canal == "correo" else None}
        if canal == "whatsapp" and not res["wa_url"]:
            res["aviso"] = ("Esta empresa no tiene un celular registrado válido. Agréguelo en Contactos (la base original no trae teléfonos) "
                            "o copie el mensaje y envíelo por otro medio.")
        if canal == "correo" and not res["mailto_url"]:
            res["aviso"] = "Esta empresa no tiene correo registrado. Agréguelo en Contactos o copie el mensaje."
        return jsonify(res)

    # ---- programador en segundo plano (solo mientras el servidor está encendido)
    def bucle():
        while True:
            try:
                with app.app_context():
                    MS.respaldo_si_toca(A)
                    h = int(MB.cfg_get(A.get_db(), "avisos", {"hora_resumen": 8}).get("hora_resumen", 8))
                    if smtp_config() and datetime.now(A.TZ).hour >= h and MB.cfg_get(A.get_db(), "ultimo_resumen", None) != A.hoy():
                        enviar_resumen()
            except Exception as e:  # noqa: BLE001
                print("[avisos] error:", e)
            time.sleep(600)

    if os.environ.get("VELTRIX_SEGUNDO_PLANO", "1") != "0" and not os.environ.get("VELTRIX_NO_BG"):
        app._bg_pendiente = bucle  # se arranca desde app.py solo con `python app.py` (no al importar en pruebas)
