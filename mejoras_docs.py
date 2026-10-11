"""Documentos comerciales: propuestas y cuentas de cobro (PDF), numeración controlada, estados e historial.

NO genera facturas electrónicas: requieren un proveedor tecnológico autorizado por la DIAN (ver /api/facturacion/estado).
"""
import hashlib
import json
import os
from datetime import datetime, timedelta

from flask import jsonify, request, send_file

import mejoras_base as MB

ESTADOS = {"propuesta": ["borrador", "generado", "enviado", "aceptado", "rechazado", "anulado"],
           "cuenta_cobro": ["borrador", "generado", "enviado", "pagado", "anulado"]}
PREFIJO = {"propuesta": "PROP", "cuenta_cobro": "CC"}
TRANSICIONES = {"propuesta": {"generado": ["enviado", "rechazado"], "enviado": ["aceptado", "rechazado"]},
                "cuenta_cobro": {"generado": ["enviado"]}}
SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos(id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, numero TEXT UNIQUE,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id), oportunidad_id INTEGER, estado TEXT NOT NULL DEFAULT 'borrador',
  titulo TEXT, datos TEXT, valor REAL DEFAULT 0, fecha_emision TEXT, fecha_vigencia TEXT, fecha_envio TEXT,
  fecha_respuesta TEXT, fecha_pago TEXT, version INTEGER DEFAULT 0, creado TEXT, actualizado TEXT);
CREATE INDEX IF NOT EXISTS ix_doc_emp ON documentos(empresa_id);
CREATE INDEX IF NOT EXISTS ix_doc_opp ON documentos(oportunidad_id);
CREATE TABLE IF NOT EXISTS documento_versiones(id INTEGER PRIMARY KEY AUTOINCREMENT, documento_id INTEGER, version INTEGER,
  archivo TEXT, fecha TEXT, sha256 TEXT);
CREATE TABLE IF NOT EXISTS secuencias(tipo TEXT, anio INTEGER, ultimo INTEGER, PRIMARY KEY(tipo, anio));
"""
EMISOR_DEF = {"nombre": "", "nombre_comercial": "VELTRIX", "documento": "", "ciudad": "Cali", "direccion": "", "telefono": "",
              "correo": "", "titular_cuenta": "", "banco": "", "tipo_cuenta": "", "numero_cuenta": "", "nota_legal_cobro": "",
              "vigencia_dias": 15, "dias_pago": 15,
              "condiciones": "Los valores se expresan en pesos colombianos (COP). Esta propuesta es válida durante el periodo "
                             "indicado. El alcance se limita a lo descrito; cambios de alcance se cotizan por separado."}


def migrate(db):
    db.executescript(SCHEMA)
    if "documento_id" not in {r[1] for r in db.execute("PRAGMA table_info(cobros)")}:
        db.execute("ALTER TABLE cobros ADD COLUMN documento_id INTEGER")


# ------------------------------------------------------------------ utilidades
_U = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece", "catorce",
      "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés",
      "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"]
_D = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
_C = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos", "ochocientos",
      "novecientos"]


def _menor_mil(n):
    if n == 100:
        return "cien"
    c, r = divmod(n, 100)
    if r < 30:
        t = _U[r]
    else:
        d, u = divmod(r, 10)
        t = _D[d] + (" y " + _U[u] if u else "")
    return (_C[c] + " " + t).strip()


def _apocope(s):
    if s.endswith("veintiuno"):
        return s[:-9] + "veintiún"
    return s[:-3] + "un" if s.endswith("uno") else s


def num_letras(n):
    n = int(round(n))
    if n == 0:
        return "cero"
    partes = []
    mm, resto = divmod(n, 10 ** 6)
    if mm:
        partes.append("un millón" if mm == 1 else _apocope(num_letras(mm)) + " millones")
    mil, u = divmod(resto, 1000)
    if mil:
        partes.append("mil" if mil == 1 else _apocope(_menor_mil(mil)) + " mil")
    if u:
        partes.append(_menor_mil(u))
    return " ".join(partes)


def cop(n):
    return "$" + f"{round(n or 0):,}".replace(",", ".")


def siguiente_numero(db, tipo, A):
    anio = int(A.hoy()[:4])
    db.execute("INSERT OR IGNORE INTO secuencias(tipo,anio,ultimo) VALUES(?,?,0)", (tipo, anio))
    db.execute("UPDATE secuencias SET ultimo=ultimo+1 WHERE tipo=? AND anio=?", (tipo, anio))
    n = db.execute("SELECT ultimo FROM secuencias WHERE tipo=? AND anio=?", (tipo, anio)).fetchone()[0]
    return f"{PREFIJO[tipo]}-{anio}-{n:04d}"


def carpeta_docs(A):
    d = os.path.join(A.DATA_DIR, "documentos")
    os.makedirs(d, exist_ok=True)
    return d


def total_inversion(datos):
    sub = sum(float(i.get("valor") or 0) for i in datos.get("inversion", []))
    return round(sub * (1 + float(datos.get("iva_pct") or 0) / 100), 2), sub


# ------------------------------------------------------------------ PDF
def generar_pdf(path, doc, empresa, emisor, datos):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    NAVY, GOLD, MUT = colors.HexColor("#0f1b2d"), colors.HexColor("#b8893a"), colors.HexColor("#6b778a")
    ss = getSampleStyleSheet()
    base = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=10, leading=14, textColor=colors.HexColor("#16202e"))
    h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=NAVY)
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=NAVY, spaceBefore=12, spaceAfter=4)
    mut = ParagraphStyle("m", parent=base, fontSize=9, textColor=MUT)
    esc = lambda s: (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def pie(c, d):
        c.saveState()
        c.setFont("Helvetica", 8)
        c.setFillColor(MUT)
        c.drawString(2 * cm, 1.2 * cm, f"{doc['numero']} · {emisor.get('nombre_comercial') or ''}")
        c.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {d.page}")
        c.restoreState()

    S = []
    sec = [0]

    def H(t):
        sec[0] += 1
        return Paragraph(f"{sec[0]}. {t}", h2)

    nom = emisor.get("nombre_comercial") or "VELTRIX"
    S.append(Paragraph(f'<font color="#b8893a">■</font> {esc(nom)}', ParagraphStyle("lg", parent=base, fontName="Helvetica-Bold", fontSize=14, textColor=NAVY)))
    S.append(Paragraph(esc(emisor.get("nombre") or ""), mut))
    S.append(HRFlowable(width="100%", color=GOLD, thickness=1.5, spaceBefore=6, spaceAfter=10))

    if doc["tipo"] == "propuesta":
        S.append(Paragraph("PROPUESTA COMERCIAL", h1))
        S.append(Paragraph(esc(datos.get("titulo") or doc.get("titulo") or ""), ParagraphStyle("t", parent=base, fontSize=12, textColor=GOLD)))
        meta = [["Número", doc["numero"], "Fecha", doc["fecha_emision"]],
                ["Cliente", empresa["razon"], "Vigencia hasta", doc["fecha_vigencia"]],
                ["NIT", empresa.get("nit") or "—", "Atención", datos.get("contacto") or "—"]]
        t = Table([[Paragraph(f"<b>{esc(a)}</b>", mut), Paragraph(esc(b), base), Paragraph(f"<b>{esc(c)}</b>", mut), Paragraph(esc(d), base)] for a, b, c, d in meta],
                  colWidths=[2.4 * cm, 6.6 * cm, 3 * cm, 5 * cm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        S += [Spacer(1, 6), t]
        if datos.get("necesidad"):
            S += [H("Necesidad identificada"), Paragraph(esc(datos["necesidad"]).replace("\n", "<br/>"), base)]
        for titulo, clave in [("Alcance", "alcance"), ("Entregables", "entregables")]:
            items = [x for x in datos.get(clave, []) if str(x).strip()]
            if items:
                S.append(H(titulo))
                S.append(ListFlowable([ListItem(Paragraph(esc(x), base)) for x in items], bulletType="bullet", start="•", leftIndent=14))
        crono = [c for c in datos.get("cronograma", []) if c.get("fase")]
        if crono:
            S.append(H("Cronograma"))
            t = Table([["Fase / actividad", "Duración"]] + [[Paragraph(esc(c["fase"]), base), esc(c.get("duracion", ""))] for c in crono], colWidths=[12 * cm, 5 * cm])
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                   ("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e6ee"))]))
            S.append(t)
        total, sub = total_inversion(datos)
        S.append(H("Inversión"))
        filas = [["Concepto", "Valor (COP)"]] + [[Paragraph(esc(i.get("concepto", "")), base), cop(float(i.get("valor") or 0))] for i in datos.get("inversion", [])]
        iva = float(datos.get("iva_pct") or 0)
        if iva:
            filas += [["Subtotal", cop(sub)], [f"IVA {iva:g}%", cop(total - sub)]]
        filas.append(["TOTAL", cop(total)])
        t = Table(filas, colWidths=[12 * cm, 5 * cm])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                               ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e6ee")), ("LINEABOVE", (0, -1), (-1, -1), 1, GOLD)]))
        S.append(t)
        if datos.get("forma_pago"):
            S.append(Paragraph(f"<b>Forma de pago:</b> {esc(datos['forma_pago'])}", base))
        S += [H("Condiciones y vigencia"),
              Paragraph(esc(datos.get("condiciones") or emisor.get("condiciones") or "").replace("\n", "<br/>"), base),
              Paragraph(f"Vigencia: hasta el {doc['fecha_vigencia']}.", base)]
        S += [Spacer(1, 30), Paragraph("_______________________________", base), Paragraph(esc(emisor.get("nombre") or ""), base),
              Paragraph(esc(emisor.get("nombre_comercial") or ""), mut)]
    else:
        S.append(Paragraph("CUENTA DE COBRO", h1))
        S.append(Paragraph(f"No. {doc['numero']}", ParagraphStyle("t", parent=base, fontSize=12, textColor=GOLD)))
        S.append(Spacer(1, 8))
        S.append(Paragraph(f"{esc(emisor.get('ciudad') or '')}, {doc['fecha_emision']}", base))
        S.append(Spacer(1, 10))
        S.append(Paragraph("<b>DEBE A:</b>", mut))
        S.append(Paragraph(f"{esc(emisor.get('nombre'))} — {esc(emisor.get('nombre_comercial'))}<br/>Documento: {esc(emisor.get('documento') or '—')}"
                           f"<br/>{esc(emisor.get('direccion') or '')} {esc(emisor.get('telefono') or '')} {esc(emisor.get('correo') or '')}", base))
        S.append(Spacer(1, 8))
        S.append(Paragraph("<b>CLIENTE:</b>", mut))
        S.append(Paragraph(f"{esc(empresa['razon'])}<br/>NIT: {esc(empresa.get('nit') or '—')}", base))
        S.append(Paragraph("Por concepto de", h2))
        S.append(Paragraph(esc(datos.get("concepto") or ""), base))
        S.append(Spacer(1, 8))
        t = Table([["VALOR A PAGAR", cop(doc["valor"])]], colWidths=[10 * cm, 7 * cm])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f6f9")), ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                               ("FONTSIZE", (0, 0), (-1, -1), 12), ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("LINEABOVE", (0, 0), (-1, 0), 1, GOLD),
                               ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        S += [t, Paragraph(f"Son: {num_letras(doc['valor']).capitalize()}{' de' if round(doc['valor']) % 10 ** 6 == 0 else ''} pesos colombianos.", base),
              Paragraph(f"Fecha límite de pago: {doc['fecha_vigencia']}", base)]
        if emisor.get("banco") or emisor.get("numero_cuenta"):
            S += [Paragraph("Datos para el pago", h2),
                  Paragraph(" · ".join(esc(emisor.get(k)) for k in ("banco", "tipo_cuenta", "numero_cuenta") if emisor.get(k)) + "<br/>"
                            f"Titular: {esc(emisor.get('titular_cuenta') or emisor.get('nombre'))}", base)]
        if emisor.get("nota_legal_cobro"):
            S += [Spacer(1, 10), Paragraph(esc(emisor["nota_legal_cobro"]).replace("\n", "<br/>"), mut)]
        S += [Spacer(1, 30), Paragraph("_______________________________", base), Paragraph(esc(emisor.get("nombre") or ""), base)]
        S.append(Spacer(1, 6))
        S.append(Paragraph("Este documento es una cuenta de cobro; no es una factura electrónica de venta.", mut))

    SimpleDocTemplate(path, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=2 * cm,
                      title=f"{doc['numero']}", author=emisor.get("nombre_comercial") or "VELTRIX").build(S, onFirstPage=pie, onLaterPages=pie)


# ------------------------------------------------------------------ API
def register(app, A):
    def emisor():
        return MB.cfg_get(A.get_db(), "emisor", EMISOR_DEF)

    def get_doc(did):
        d = A.one("SELECT * FROM documentos WHERE id=?", (did,))
        if not d:
            raise A.ApiError("Documento no encontrado.", 404)
        return d

    def decorar(d):
        d["datos"] = json.loads(d["datos"] or "{}")
        d["estado_efectivo"] = d["estado"]
        if d["estado"] in ("generado", "enviado") and d["fecha_vigencia"] and d["fecha_vigencia"] < A.hoy():
            d["estado_efectivo"] = "vencido"
        d["cobrado"] = A.one("SELECT COALESCE(SUM(monto),0) n FROM cobros WHERE documento_id=?", (d["id"],))["n"]
        d["saldo"] = max((d["valor"] or 0) - d["cobrado"], 0)
        return d

    @app.get("/api/config/emisor")
    def get_emisor():
        return jsonify(emisor())

    @app.put("/api/config/emisor")
    def put_emisor():
        cfg = emisor()
        for k in EMISOR_DEF:
            if k in A.body():
                v = A.body()[k]
                cfg[k] = int(v) if k in ("vigencia_dias", "dias_pago") else (str(v).strip() if v is not None else "")
        MB.cfg_set(A.get_db(), "emisor", cfg)
        MB.audit(A.get_db(), "config", 0, "emisor", None, "actualizado")
        A.get_db().commit()
        return jsonify(cfg)

    @app.get("/api/facturacion/estado")
    def fact_estado():
        return jsonify({"factura_electronica": False,
                        "explicacion": "El CRM NO genera facturas electrónicas de venta. En Colombia la factura electrónica debe emitirse y "
                                       "validarse mediante un software o proveedor tecnológico habilitado por la DIAN (con resolución de numeración "
                                       "y firma). Para habilitarla hay que contratar un proveedor y conectar su API; este CRM solo registra la "
                                       "referencia del cobro. Si usted es persona natural, confirme con su contador si está obligado a facturar."})

    @app.get("/api/documentos")
    def listar_docs():
        a = request.args
        w, p = [], []
        for k, col in (("tipo", "d.tipo"), ("estado", "d.estado"), ("empresa_id", "d.empresa_id"), ("oportunidad_id", "d.oportunidad_id")):
            if a.get(k):
                w.append(f"{col}=?")
                p.append(a[k])
        rs = A.rows(f"""SELECT d.*, e.razon FROM documentos d JOIN empresas e ON e.id=d.empresa_id
            {'WHERE ' + ' AND '.join(w) if w else ''} ORDER BY d.id DESC LIMIT 300""", p)
        out = [decorar(r) for r in rs]
        if a.get("estado_efectivo"):
            out = [r for r in out if r["estado_efectivo"] == a["estado_efectivo"]]
        return jsonify(out)

    @app.get("/api/documentos/<int:did>")
    def ver_doc(did):
        d = decorar(get_doc(did))
        d["versiones"] = A.rows("SELECT version,archivo,fecha,sha256 FROM documento_versiones WHERE documento_id=? ORDER BY version DESC", (did,))
        d["empresa"] = A.one("SELECT id,razon,nit FROM empresas WHERE id=?", (d["empresa_id"],))
        return jsonify(d)

    def validar_datos(tipo, datos, valor_cobro=None):
        if tipo == "propuesta":
            tot, _ = total_inversion(datos)
            if tot <= 0:
                raise A.ApiError("La propuesta necesita al menos un concepto de inversión con valor.")
            if not (datos.get("titulo") or "").strip():
                raise A.ApiError("Indique el título de la propuesta.")
            return tot
        if not (datos.get("concepto") or "").strip():
            raise A.ApiError("Indique el concepto de la cuenta de cobro.")
        return valor_cobro

    @app.post("/api/documentos")
    def crear_doc():
        d = A.body()
        tipo = d.get("tipo")
        if tipo not in ESTADOS:
            raise A.ApiError("Tipo de documento no válido (propuesta o cuenta_cobro).")
        opp = None
        if d.get("oportunidad_id"):
            opp = A.one("SELECT * FROM oportunidades WHERE id=?", (d["oportunidad_id"],))
        elif d.get("empresa_id"):
            opp = A.one("SELECT * FROM oportunidades WHERE empresa_id=? ORDER BY (etapa IN ('Ganado','Perdido')), id DESC", (d["empresa_id"],))
        if not opp:
            raise A.ApiError("Primero agregue la empresa al pipeline: el documento se asocia a una oportunidad.")
        datos = d.get("datos") or {}
        valor = 0
        if tipo == "propuesta":
            if opp["etapa"] in ("Ganado", "Perdido"):
                raise A.ApiError("La oportunidad está cerrada; reábrala o cree una nueva para proponer.")
            valor = total_inversion(datos)[0]
        else:
            if opp["etapa"] != "Ganado":
                raise A.ApiError("La cuenta de cobro se emite sobre una venta ganada.")
            documentado = A.one("SELECT COALESCE(SUM(valor),0) n FROM documentos WHERE oportunidad_id=? AND tipo='cuenta_cobro' AND estado<>'anulado'", (opp["id"],))["n"]
            valor = float(d.get("valor") or ((opp["valor_cerrado"] or 0) - documentado))
            if valor <= 0:
                raise A.ApiError("No hay saldo por documentar en esta venta.")
            if valor > (opp["valor_cerrado"] or 0) - documentado + 0.5:
                raise A.ApiError(f"El valor supera lo que falta por documentar ({cop((opp['valor_cerrado'] or 0) - documentado)}).")
        cur = A.get_db().execute("""INSERT INTO documentos(tipo,empresa_id,oportunidad_id,estado,titulo,datos,valor,creado,actualizado)
            VALUES(?,?,?,?,?,?,?,?,?)""", (tipo, opp["empresa_id"], opp["id"], "borrador", datos.get("titulo") or datos.get("concepto"),
                                           json.dumps(datos, ensure_ascii=False), valor, A.ahora(), A.ahora()))
        MB.audit(A.get_db(), "documento", cur.lastrowid, "estado", None, "borrador")
        A.get_db().commit()
        return jsonify({"id": cur.lastrowid}), 201

    @app.put("/api/documentos/<int:did>")
    def editar_doc(did):
        doc = get_doc(did)
        if doc["estado"] not in ("borrador", "generado"):
            raise A.ApiError("Solo se editan documentos en borrador o generados (aún no enviados).", 409)
        datos = A.body().get("datos") or {}
        valor = doc["valor"]
        if doc["tipo"] == "propuesta":
            valor = total_inversion(datos)[0]
        elif "valor" in A.body():
            valor = float(A.body()["valor"])
        A.get_db().execute("UPDATE documentos SET datos=?, valor=?, titulo=?, actualizado=? WHERE id=?",
                           (json.dumps(datos, ensure_ascii=False), valor, datos.get("titulo") or datos.get("concepto"), A.ahora(), did))
        A.get_db().commit()
        return jsonify({"ok": True})

    @app.post("/api/documentos/<int:did>/generar")
    def generar_doc(did):
        doc = get_doc(did)
        if doc["estado"] not in ("borrador", "generado"):
            raise A.ApiError("Un documento enviado no se regenera; anúlelo y cree uno nuevo si debe cambiar.", 409)
        em = emisor()
        if not em.get("nombre"):
            raise A.ApiError("Complete los datos del emisor en Configuración antes de generar documentos.")
        datos = json.loads(doc["datos"] or "{}")
        valor = validar_datos(doc["tipo"], datos, doc["valor"])
        db = A.get_db()
        numero = doc["numero"] or siguiente_numero(db, doc["tipo"], A)
        emision = doc["fecha_emision"] or A.hoy()
        dias = int(datos.get("vigencia_dias") or em.get("vigencia_dias") or 15) if doc["tipo"] == "propuesta" else int(datos.get("dias_pago") or em.get("dias_pago") or 15)
        venc = (datetime.strptime(emision, "%Y-%m-%d") + timedelta(days=dias)).strftime("%Y-%m-%d")
        v = (doc["version"] or 0) + 1
        doc.update(numero=numero, fecha_emision=emision, fecha_vigencia=venc, valor=valor)
        emp = A.one("SELECT * FROM empresas WHERE id=?", (doc["empresa_id"],))
        archivo = f"{numero}_v{v}.pdf"
        path = os.path.join(carpeta_docs(A), archivo)
        try:
            generar_pdf(path, doc, emp, em, datos)
        except ImportError:
            raise A.ApiError("Falta la librería reportlab. Ejecute: pip install reportlab", 500)
        sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        db.execute("UPDATE documentos SET numero=?, estado='generado', valor=?, fecha_emision=?, fecha_vigencia=?, version=?, actualizado=? WHERE id=?",
                   (numero, valor, emision, venc, v, A.ahora(), did))
        db.execute("INSERT INTO documento_versiones(documento_id,version,archivo,fecha,sha256) VALUES(?,?,?,?,?)", (did, v, archivo, A.ahora(), sha))
        if doc["tipo"] == "propuesta":
            o = A.one("SELECT * FROM oportunidades WHERE id=?", (doc["oportunidad_id"],))
            if o and o["etapa"] in A.ACTIVAS and o["valor_estimado"] != valor:
                db.execute("UPDATE oportunidades SET valor_estimado=?, actualizado=? WHERE id=?", (valor, A.ahora(), o["id"]))
                MB.audit(db, "oportunidad", o["id"], "valor_estimado", o["valor_estimado"], valor, f"propuesta {numero}")
        MB.audit(db, "documento", did, "estado", doc["estado"], "generado", f"{numero} v{v}")
        db.commit()
        return jsonify({"numero": numero, "version": v, "archivo": archivo})

    @app.get("/api/documentos/<int:did>/pdf")
    def bajar_pdf(did):
        doc = get_doc(did)
        v = request.args.get("version")
        r = A.one("SELECT archivo FROM documento_versiones WHERE documento_id=? " + ("AND version=?" if v else "") + " ORDER BY version DESC",
                  (did, int(v)) if v else (did,))
        if not r:
            raise A.ApiError("Aún no se ha generado el PDF.", 404)
        return send_file(os.path.join(carpeta_docs(A), r["archivo"]), as_attachment=True, download_name=r["archivo"], mimetype="application/pdf")

    @app.post("/api/documentos/<int:did>/estado")
    def estado_doc(did):
        doc = get_doc(did)
        d = A.body()
        nuevo = d.get("estado")
        db = A.get_db()
        f = d.get("fecha") or A.hoy()
        if not A.valid_date(f):
            raise A.ApiError("Fecha no válida.")
        if nuevo == "anulado":
            if doc["estado"] == "pagado" or A.one("SELECT 1 x FROM cobros WHERE documento_id=?", (did,)):
                raise A.ApiError("No se anula un documento con cobros registrados.", 409)
        elif nuevo == "pagado":
            raise A.ApiError("«Pagado» se asigna solo al registrar cobros reales contra este documento (Pipeline > + Cobro).", 409)
        elif nuevo not in TRANSICIONES.get(doc["tipo"], {}).get(doc["estado"], []):
            raise A.ApiError(f"No se puede pasar de «{doc['estado']}» a «{nuevo}».", 409)
        campo = {"enviado": "fecha_envio", "aceptado": "fecha_respuesta", "rechazado": "fecha_respuesta"}.get(nuevo)
        db.execute(f"UPDATE documentos SET estado=?, actualizado=?{', ' + campo + '=?' if campo else ''} WHERE id=?",
                   (nuevo, A.ahora()) + ((f,) if campo else ()) + (did,))
        MB.audit(db, "documento", did, "estado", doc["estado"], nuevo, d.get("nota"))
        res = {"ok": True}
        o = A.one("SELECT * FROM oportunidades WHERE id=?", (doc["oportunidad_id"],))
        if nuevo == "enviado" and doc["tipo"] == "propuesta" and o and o["etapa"] in A.ACTIVAS:
            canal = d.get("canal") if d.get("canal") in A.TIPOS_INT else "Correo"
            paso, fpaso = f"Seguimiento de propuesta {doc['numero']}", (datetime.strptime(f, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")
            if o["etapa"] != "Propuesta enviada":
                db.execute("UPDATE oportunidades SET etapa='Propuesta enviada' WHERE id=?", (o["id"],))
                A.registrar_etapa(o["id"], "Propuesta enviada")
            primer = 0 if A.one("SELECT 1 x FROM interacciones WHERE empresa_id=?", (doc["empresa_id"],)) else 1
            db.execute("UPDATE oportunidades SET proximo_paso=?, proxima_fecha=?, ultima_interaccion=?, actualizado=? WHERE id=?", (paso, fpaso, f, A.ahora(), o["id"]))
            db.execute("""INSERT INTO interacciones(empresa_id,oportunidad_id,tipo,resultado,notas,fecha,creado,primer_contacto)
                VALUES(?,?,?,?,?,?,?,?)""", (doc["empresa_id"], o["id"], canal, "Propuesta enviada", doc["numero"], f, A.ahora(), primer))
            res["oportunidad"] = f"Movida a «Propuesta enviada»; seguimiento programado para {fpaso}."
        if nuevo == "aceptado":
            res["sugerencia"] = {"accion": "ganar", "oportunidad_id": doc["oportunidad_id"], "valor": doc["valor"]}
        db.commit()
        return jsonify(res)

    def recalcular_pago(did):
        """Marca 'pagado' solo si los cobros registrados cubren el valor del documento."""
        db = A.get_db()
        doc = A.one("SELECT * FROM documentos WHERE id=?", (did,))
        if not doc or doc["estado"] == "anulado":
            return
        pagado = A.one("SELECT COALESCE(SUM(monto),0) n, MAX(fecha) f FROM cobros WHERE documento_id=?", (did,))
        if pagado["n"] >= (doc["valor"] or 0) - 0.5 and pagado["n"] > 0:
            if doc["estado"] != "pagado":
                db.execute("UPDATE documentos SET estado='pagado', fecha_pago=?, actualizado=? WHERE id=?", (pagado["f"], A.ahora(), did))
                MB.audit(db, "documento", did, "estado", doc["estado"], "pagado", "cobros registrados")
        elif doc["estado"] == "pagado":
            nuevo = "enviado" if doc["fecha_envio"] else "generado"
            db.execute("UPDATE documentos SET estado=?, fecha_pago=NULL, actualizado=? WHERE id=?", (nuevo, A.ahora(), did))
            MB.audit(db, "documento", did, "estado", "pagado", nuevo, "cobro eliminado")
        db.commit()

    A.recalcular_pago_doc = recalcular_pago
