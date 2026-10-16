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


def enviar_correo(asunto, cuerpo, conf=None, html=None):
    c = conf or smtp_config()
    if not c:
        raise RuntimeError("SMTP no configurado (variables VELTRIX_SMTP_HOST, _PORT, _USER, _PASS, _FROM, _TO).")
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = asunto, c.get("from") or c["user"], c["to"]
    m.set_content(cuerpo)
    if html:
        m.add_alternative(html, subtype="html")
    with smtplib.SMTP(c["host"], int(c.get("port") or 587), timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
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

    def agenda_texto():
        agenda = A.construir_agenda(A.hoy())
        lineas = [
            f"VELTRIX | AGENDA EJECUTIVA · {agenda['fecha']}",
            "",
            "META COMERCIAL DIARIA",
            "Ventas cerradas: $1.000.000 COP (meta configurable en el CRM)",
            "Dinero cobrado: $1.000.000 COP (meta configurable en el CRM)",
            "",
            "AGENDA POR BLOQUES"
        ]

        for bloque in agenda["bloques"]:
            lineas.append(
                f"\n{bloque['inicio']}-{bloque['fin']} | {bloque['nombre']}"
            )
            for objetivo in bloque["objetivos"]:
                meta = objetivo.get("meta", "")
                unidad = objetivo.get("unidad", "")
                avance = objetivo.get("avance_dia")
                texto = f"- {objetivo['nombre']}: meta {meta} {unidad}".rstrip()
                if avance is not None:
                    texto += f" | avance del día: {avance}"
                lineas.append(texto)

            for compromiso in bloque.get("compromisos", []):
                lineas.append(
                    f"- COMPROMISO: {compromiso['titulo']} "
                    f"{compromiso.get('hora') or ''} — "
                    f"{compromiso.get('detalle') or ''}"
                )

        if agenda["sin_horario"]:
            lineas.extend(["", "COMPROMISOS SIN BLOQUE HORARIO"])
            for item in agenda["sin_horario"]:
                lineas.append(
                    f"- {item['fecha']} {item.get('hora') or ''} "
                    f"{item['titulo']} — {item.get('detalle') or ''}"
                )

        lineas.extend([
            "",
            "REPORTE COMERCIAL DEL CRM",
            agenda["reporte"]["texto"],
            "",
            "ALERTAS Y SEGUIMIENTOS",
            resumen_texto(),
            "",
            "Nota: la agenda propone metas por bloque; el CRM mide el avance diario."
        ])
        return "\n".join(lineas)

    A.agenda_texto = agenda_texto

    def agenda_html():
        from html import escape

        agenda = A.construir_agenda(A.hoy())
        r = agenda.get("reporte", {})

        def e(v):
            return escape(str(v if v is not None else ""), quote=True)

        def cop(v):
            try:
                return "COP $" + f"{round(float(v)):,}".replace(",", ".")
            except (TypeError, ValueError):
                return "Sin datos"

        def money_metric(obj, key="cop"):
            if not isinstance(obj, dict) or key not in obj or obj[key] is None:
                return "Sin datos"
            return cop(obj[key])

        ventas = r.get("ventas_cerradas") or {}
        cobros = r.get("dinero_cobrado") or {}
        pipeline = r.get("pipeline") or {}
        pendientes = r.get("pendientes") or {}
        actividad = r.get("actividad") or {}

        def kpi(title, value, detail, accent):
            return (
                '<td width="50%" valign="top" style="padding:5px">'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                'style="border:1px solid #dce5ef;border-radius:10px;border-collapse:separate">'
                f'<tr><td style="padding:13px 14px 12px;border-top:3px solid {accent};'
                'border-radius:9px;background:#ffffff">'
                f'<div style="font:11px Arial,sans-serif;letter-spacing:.7px;color:#64748b">'
                f'{e(title)}</div>'
                f'<div style="font:bold 21px Arial,sans-serif;color:#101c2e;margin:8px 0">'
                f'{e(value)}</div>'
                f'<div style="font:12px Arial,sans-serif;line-height:1.4;color:#64748b">'
                f'{e(detail)}</div>'
                '</td></tr></table></td>'
            )

        def progress(obj):
            if not isinstance(obj, dict):
                return "Avance no disponible"
            actual = obj.get("cop")
            meta = obj.get("meta_cop")
            if actual is None or meta is None:
                return "Avance no disponible"
            try:
                actual, meta = float(actual), float(meta)
                if meta <= 0:
                    return f"Real: {cop(actual)} · Meta: {cop(meta)}"
                pct = max(0, min(100, round(actual / meta * 100)))
                return f"{pct}% cumplido · Faltan {cop(max(0, meta-actual))}"
            except (TypeError, ValueError):
                return "Avance no disponible"

        kpis = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse"><tr>'
            + kpi("VENTAS CERRADAS", money_metric(ventas),
                  progress(ventas), "#6366f1")
            + kpi("DINERO COBRADO", money_metric(cobros),
                  progress(cobros), "#13b8a6")
            + '</tr><tr>'
            + kpi("PIPELINE ACTIVO",
                  cop(pipeline.get("valor")) if "valor" in pipeline else "Sin datos",
                  (f'{pipeline.get("n")} oportunidades' if "n" in pipeline
                   else "Cantidad no disponible"), "#4f8cff")
            + kpi("SEGUIMIENTOS VENCIDOS",
                  str(pendientes["seguimientos_vencidos"])
                  if "seguimientos_vencidos" in pendientes else "Sin datos",
                  (f'Sin próximo paso: {pendientes["sin_proximo_paso"]}'
                   if "sin_proximo_paso" in pendientes else "Revisar próximos pasos"),
                  "#f59e0b")
            + '</tr></table>'
        )

        # Tres prioridades como máximo, derivadas de los objetivos existentes.
        prioridades = []
        vistos = set()
        for bloque in agenda.get("bloques", []):
            for objetivo in bloque.get("objetivos", []):
                nombre = str(objetivo.get("nombre", "")).strip()
                if nombre and nombre.lower() not in vistos:
                    vistos.add(nombre.lower())
                    detalle = " ".join(
                        str(objetivo.get(k, "")).strip()
                        for k in ("detalle", "descripcion")
                        if objetivo.get(k)
                    )
                    meta = objetivo.get("meta")
                    unidad = objetivo.get("unidad", "")
                    if meta not in (None, ""):
                        detalle = (detalle + " · " if detalle else "") + f"Meta: {meta} {unidad}".strip()
                    prioridades.append((nombre, detalle))
                if len(prioridades) >= 3:
                    break
            if len(prioridades) >= 3:
                break

        if not prioridades:
            prioridades = [
                ("Activar el pipeline comercial", "Registrar prospectos reales y definir el siguiente paso."),
                ("Avanzar oportunidades", "Priorizar conversaciones y propuestas con potencial de cierre."),
                ("Gestionar cobros", "Verificar compromisos de pago y dinero recibido.")
            ]

        priority_html = []
        for i, (nombre, detalle) in enumerate(prioridades, 1):
            tint, ink = [("#dff8f3", "#087f73"),
                         ("#e8eafe", "#4f46e5"),
                         ("#fff1dc", "#a65d08")][i-1]
            priority_html.append(
                '<tr><td width="42" valign="top" style="padding:7px 8px 7px 0">'
                f'<div style="background:{tint};color:{ink};text-align:center;'
                'font:bold 14px Arial;padding:10px 0;border-radius:8px">'
                f'{i:02d}</div></td>'
                '<td valign="top" style="padding:8px 0;border-bottom:1px solid #e8edf4">'
                f'<div style="font:bold 14px Arial;color:#17263a">{e(nombre)}</div>'
                f'<div style="font:12px Arial;line-height:1.5;color:#64748b;margin-top:3px">'
                f'{e(detalle or "Ejecutar y registrar el resultado en el CRM.")}</div>'
                '</td></tr>'
            )

        # Cada bloque muestra como máximo dos acciones breves.
        blocks_html = []
        for b in agenda.get("bloques", []):
            items = []
            for o in b.get("objetivos", []):
                label = str(o.get("nombre", "")).strip()
                if label:
                    meta = o.get("meta")
                    unidad = o.get("unidad", "")
                    suffix = f" · Meta: {meta} {unidad}".strip() if meta not in (None, "") else ""
                    items.append(label + suffix)
                if len(items) >= 2:
                    break
            for c in b.get("compromisos", []):
                if len(items) >= 2:
                    break
                title = str(c.get("titulo", "")).strip()
                if title:
                    items.append(title)
            if not items:
                items = ["Revisar prioridades comerciales y registrar avances."]
            rows = "".join(
                '<tr><td style="padding:5px 0;font:13px Arial;line-height:1.45;color:#334155">'
                f'<span style="color:#0d9488;font-weight:bold">›</span> {e(item)}</td></tr>'
                for item in items
            )
            blocks_html.append(
                '<tr><td width="78" valign="top" style="padding:12px 8px 12px 0;'
                'font:bold 12px Arial;color:#0d9488;white-space:nowrap">'
                f'{e(b.get("inicio", ""))}<br><span style="color:#94a3b8;font-weight:normal">'
                f'{e(b.get("fin", ""))}</span></td>'
                '<td valign="top" style="padding:9px 0;border-bottom:1px solid #e5ebf2">'
                f'<div style="font:bold 14px Arial;color:#17263a">{e(b.get("nombre", "Bloque"))}</div>'
                f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
                f'{rows}</table></td></tr>'
            )

        # Máximo tres alertas urgentes; no imprimir el informe completo.
        alertas = notificaciones()
        urgent_items = [
            i for i in alertas.get("items", [])
            if i.get("nivel") in ("vencido", "hoy")
        ][:3]
        alert_rows = []
        for item in urgent_items:
            is_late = item.get("nivel") == "vencido"
            color = "#b91c1c" if is_late else "#a16207"
            label = "VENCIDO" if is_late else "HOY"
            alert_rows.append(
                '<tr><td style="padding:8px 0;border-bottom:1px solid #f1e5d6;'
                'font:12px Arial;line-height:1.45">'
                f'<strong style="color:{color}">{label} · {e(item.get("fecha", ""))}</strong>'
                f'<div style="color:#334155;margin-top:3px">{e(item.get("titulo", ""))}</div>'
                '</td></tr>'
            )
        if not alert_rows:
            alert_rows.append(
                '<tr><td style="padding:8px 0;font:12px Arial;color:#64748b">'
                'No hay alertas críticas registradas para hoy.</td></tr>'
            )

        fecha = e(agenda.get("fecha", A.hoy()))
        return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>VELTRIX | Executive Command Center</title>
</head>
<body style="margin:0;padding:0;background:#edf1f6;font-family:Arial,Helvetica,sans-serif;color:#17263a">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#edf1f6">
<tr><td align="center" style="padding:18px 8px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="max-width:680px;border-collapse:separate;background:#ffffff;border-radius:14px;overflow:hidden">

<tr><td bgcolor="#0b1220" style="padding:24px 22px 22px;color:#ffffff">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td valign="middle">
<div style="font:bold 22px Arial;letter-spacing:2px;color:#ffffff">VELTRIX<span style="color:#13d6c5">.</span></div>
<div style="font:10px Arial;letter-spacing:1.5px;color:#a7b7cd;margin-top:5px">EXECUTIVE COMMAND CENTER</div>
</td>
<td align="right" valign="middle">
<span style="background:#123b40;color:#69f0df;border-radius:20px;padding:7px 9px;font:bold 10px Arial">AGENDA DIARIA</span>
</td></tr></table>
<div style="font:11px Arial;letter-spacing:1px;color:#a7b7cd;margin-top:22px">{fecha}</div>
<div style="font:bold 25px Arial;line-height:1.2;color:#ffffff;margin-top:7px">Hoy se ejecuta para vender.</div>
<div style="font:13px Arial;line-height:1.5;color:#cbd5e1;margin-top:8px">Foco del día: convertir oportunidades comerciales en ventas y dinero efectivamente cobrado.</div>
</td></tr>

<tr><td bgcolor="#101c2e" style="padding:0 17px 18px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="border:1px solid #2d4158;border-radius:10px;background:#17263a">
<tr><td style="padding:15px 16px">
<div style="font:11px Arial;letter-spacing:1px;color:#a7b7cd">OBJETIVO FINANCIERO</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="padding-top:7px">
<div style="font:bold 23px Arial;color:#ffffff">Ventas: {e(money_metric(ventas))}</div>
<div style="font:bold 18px Arial;color:#6de5d5;margin-top:5px">Cobros: {e(money_metric(cobros))}</div>
</td>
<td align="right" valign="middle" style="padding-left:8px">
<div style="font:11px Arial;color:#a7b7cd">Meta ventas</div>
<div style="font:bold 13px Arial;color:#ffffff">{e(cop(ventas.get("meta_cop")))}</div>
<div style="font:11px Arial;color:#a7b7cd;margin-top:7px">Meta cobros</div>
<div style="font:bold 13px Arial;color:#ffffff">{e(cop(cobros.get("meta_cop")))}</div>
</td></tr></table>
</td></tr></table>
</td></tr>

<tr><td style="padding:17px 17px 0">
<div style="font:bold 15px Arial;color:#17263a;margin:0 0 7px">PANORAMA DEL NEGOCIO</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{kpis}</table>
</td></tr>

<tr><td style="padding:19px 22px 0">
<div style="font:bold 15px Arial;color:#17263a;margin-bottom:6px">TUS 3 PRIORIDADES</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{''.join(priority_html)}
</table>
</td></tr>

<tr><td style="padding:20px 22px 0">
<div style="font:bold 15px Arial;color:#17263a;margin-bottom:5px">RUTA DE EJECUCIÓN</div>
<div style="font:12px Arial;color:#64748b;margin-bottom:5px">Cuatro bloques. Máximo dos acciones por bloque.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{''.join(blocks_html)}
</table>
</td></tr>

<tr><td style="padding:19px 22px 0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="border:1px solid #f2dfbf;border-radius:9px;background:#fffaf1">
<tr><td style="padding:12px 14px">
<div style="font:bold 12px Arial;color:#a16207">ALERTAS QUE REQUIEREN ATENCIÓN</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{''.join(alert_rows)}
</table>
</td></tr></table>
</td></tr>

<tr><td style="padding:18px 22px 22px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="background:#0b1220;border-radius:9px">
<tr><td style="padding:13px 15px">
<div style="font:bold 12px Arial;color:#69f0df">REGLA DEL DÍA</div>
<div style="font:12px Arial;line-height:1.6;color:#ffffff;margin-top:5px">
Cada acción debe acercarnos a una venta, un cierre o un cobro. Los resultados se registran en el CRM.
</div>
</td></tr></table>
<div style="font:10px Arial;line-height:1.5;color:#94a3b8;text-align:center;margin-top:12px">
Generado desde los datos disponibles en el CRM VELTRIX.
</div>
</td></tr>
</table>
</td></tr></table>
</body></html>"""

    A.agenda_html = agenda_html

    def enviar_resumen():
        cuerpo = agenda_texto()
        enviar_correo(
            f"VELTRIX · agenda ejecutiva y alertas · {A.hoy()}",
            cuerpo,
            html=agenda_html()
        )
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
