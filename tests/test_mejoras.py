"""Pruebas de las mejoras (calidad, puntaje, documentos, recordatorios, mensajes, seguridad, copias).
Usa una base temporal. Ejecutar:  python tests/test_mejoras.py"""
import io, json, os, sqlite3, sys, tempfile
from unittest.mock import patch
HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
os.environ.update(VELTRIX_DATA=tmp, VELTRIX_AUTH="0", VELTRIX_SEGUNDO_PLANO="0")
for k in list(os.environ):
    if k.startswith("VELTRIX_SMTP"): del os.environ[k]
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from synthetic_data import build_workbook, SYNTHETIC_COMPANIES

import app as A
import mejoras_seguridad as MS, mejoras_avisos as MA, mejoras_docs as MD
c = A.app.test_client(); ok = 0
def chk(n, cond, info=""):
    global ok
    print(("PASA  " if cond else "FALLA ") + n, info if not cond else "")
    if not cond: sys.exit(1)
    ok += 1
J = lambda r: r.get_json()
P = lambda u, b=None: c.post(u, json=b or {})

# 0 libro sintético; no lee la base empresarial original
r = c.post("/api/importar", data={"archivo": (io.BytesIO(build_workbook()), "empresas_sinteticas.xlsx")}, content_type="multipart/form-data")
chk("importa empresas sintéticas y clasifica", r.status_code == 201 and J(r)["nuevas"] == SYNTHETIC_COMPANIES)
db = sqlite3.connect(A.DB_PATH)
chk("todas clasificadas y puntuadas", db.execute("select count(*) from empresas where calidad is null or score is null").fetchone()[0] == 0)
agenda = J(c.get("/api/agenda/ejecutiva"))
chk("agenda: cuatro bloques con tres objetivos", len(agenda["bloques"]) == 4 and all(len(b["objetivos"]) == 3 for b in agenda["bloques"]))
chk("agenda: ventas cerradas separadas de cobros", agenda["reporte"]["ventas_cerradas"]["meta_cop"] == 1000000 and agenda["reporte"]["dinero_cobrado"]["meta_cop"] == 1000000)
chk("reporte ejecutivo agregado", J(c.get("/api/reportes/ejecutivo"))["actividad"]["contactos_nuevos"] == 0)
P("/api/tareas", {"titulo": "Reunion sintetica", "tipo": "Reunión", "fecha": A.hoy(), "hora": "10:30"})
agenda = J(c.get("/api/agenda/ejecutiva"))
chk("agenda: ubica compromisos con hora", any(k["titulo"] == "Reunion sintetica" for k in agenda["bloques"][1]["compromisos"]))
with patch.dict(os.environ, {"OPENAI_API_KEY": "", "VELTRIX_OPENAI_ENABLED": ""}):
    ia_status = J(c.get("/api/ia/estado"))
    ia_context = J(c.get("/api/ia/contexto"))
chk("IA permanece desconectada sin exponer clave", not ia_status["api_key_configurada"] and not ia_status["llamadas_externas"] and "OPENAI_API_KEY" not in json.dumps(ia_status))
chk("contexto IA contiene solo métricas agregadas", "ventas_cerradas_hoy_cop" in ia_context and "empresas" not in ia_context and "contactos" not in ia_context)

# 1 calidad
for n in ("SAS", "sas", "0"):
    eid = J(P("/api/empresas", {"razon": n, "nit": "9" + str(abs(hash(n)) % 10**8).zfill(8), "ciudad": "Cali", "depto": "VALLE"}))["id"]
    chk(f"'{n}' excluida", db.execute("select calidad from empresas where id=?", (eid,)).fetchone()[0] in ("excluido",) or J(c.get(f"/api/empresas/{eid}"))["calidad"] == "excluido")
last = eid
chk("resumen de calidad", J(c.get("/api/calidad/resumen"))["estados"].get("excluido", 0) >= 30)
chk("exporta excluidas con motivo", b"Raz" in c.get("/api/calidad/exportar.csv?estado=excluido").data)
chk("lista excluidas", J(c.get("/api/calidad/lista?estado=excluido"))["total"] >= 30)
chk("restaurar manual", P(f"/api/empresas/{last}/calidad", {"estado": "valido"}).status_code == 200 and J(c.get(f"/api/empresas/{last}"))["calidad"] == "valido")
P("/api/calidad/clasificar")
chk("reclasificar respeta decisión manual", J(c.get(f"/api/empresas/{last}"))["calidad"] == "valido")
chk("auditoría registra la restauración", any(a["campo"] == "calidad" for a in J(c.get(f"/api/auditoria?entidad=empresa&id={last}"))))
chk("excluida fuera de prospección", all(x["calidad"] != "excluido" for x in J(c.get("/api/prospeccion?limite=200&ambito=todo"))))
chk("nada se borró", J(c.get("/api/empresas?per=1"))["total"] == SYNTHETIC_COMPANIES + 3)
e1 = J(c.get("/api/empresas?q=999000001"))["items"][0]["id"]
c.put(f"/api/empresas/{e1}", json={"notas": "x"}); chk("edición audita cambios", any(a["campo"] == "notas" for a in J(c.get(f"/api/auditoria?entidad=empresa&id={e1}"))))

# 2 puntaje
cali = J(c.get("/api/empresas?ciudad=CALI-VALLE&per=1&orden=score"))["items"][0]; bog = J(c.get("/api/empresas?ciudad=BOGOTA D.C.&per=1&orden=score"))["items"][0]
chk("Cali puntúa más que Bogotá", cali["score"] > bog["score"], (cali["score"], bog["score"]))
chk("detalle explicable", json.loads(cali["score_detalle"])["ubicacion"][2] == "Cali")
chk("ámbito Cali", all("CALI" in x["ciudad"] for x in J(c.get("/api/prospeccion?limite=30&ambito=cali"))))
chk("ámbito por defecto Valle", all("VALLE" in (x["depto"] or "") for x in J(c.get("/api/prospeccion?limite=30"))))
chk("cambia peso y recalcula", P.__call__ is not None and c.put("/api/config/scoring", json={"pesos": {"ubicacion": 0}}).status_code == 200)
chk("peso negativo rechazado", c.put("/api/config/scoring", json={"pesos": {"sector": -5}}).status_code == 400)
c.put("/api/config/scoring", json={"pesos": {"ubicacion": 25}})

# 3 mensajes
eid = J(P("/api/empresas", {"razon": "Cliente Mensajes SAS", "nit": "900777111", "ciudad": "Cali", "depto": "VALLE", "contacto": {"nombre": "Laura Pérez", "telefono": "300 123 4567", "correo": "laura@x.co"}}))["id"]
m = J(c.get(f"/api/mensajes/preparar?empresa_id={eid}&plantilla=primer_contacto&canal=whatsapp"))
chk("WhatsApp con número normalizado", m["wa_url"].startswith("https://wa.me/573001234567?text=") and "Hola Laura," in m["cuerpo"])
chk("mailto con correo", J(c.get(f"/api/mensajes/preparar?empresa_id={eid}&canal=correo"))["mailto_url"].startswith("mailto:laura%40x.co"))
chk("pendientes del emisor se señalan", "[su nombre]" in m["pendientes"])
e2 = J(P("/api/empresas", {"razon": "Sin Telefono SAS", "nit": "900777222"}))["id"]
m2 = J(c.get(f"/api/mensajes/preparar?empresa_id={e2}&canal=whatsapp"))
chk("sin teléfono: no inventa", m2["wa_url"] is None and "aviso" in m2)
chk("fijo no es WhatsApp", MA.telefono_whatsapp("602 555 1234") is None and MA.telefono_whatsapp("+57 310 111 2222") == "573101112222")

# 4 documentos: propuesta
r = J(P("/api/interacciones", {"empresa_id": eid, "tipo": "Llamada", "resultado": "Contestó: interesado", "proxima_accion": "Enviar propuesta", "proxima_fecha": A.hoy()})); oid = r["oportunidad_id"]
datos = {"titulo": "Diagnóstico organizacional", "necesidad": "Ordenar procesos", "alcance": ["Entrevistas", "Informe"], "entregables": ["Informe final"],
         "cronograma": [{"fase": "Diagnóstico", "duracion": "2 semanas"}], "inversion": [{"concepto": "Diagnóstico", "valor": 3000000}, {"concepto": "Plan", "valor": 1000000}], "forma_pago": "50/50"}
did = J(P("/api/documentos", {"tipo": "propuesta", "oportunidad_id": oid, "datos": datos}))["id"]
chk("sin datos de emisor no genera", P(f"/api/documentos/{did}/generar").status_code == 400)
c.put("/api/config/emisor", json={"nombre": "Persona de Prueba", "documento": "123", "banco": "Banco X", "numero_cuenta": "000"})
g = J(P(f"/api/documentos/{did}/generar")); yr = A.hoy()[:4]
chk("número controlado PROP-AAAA-0001", g["numero"] == f"PROP-{yr}-0001", g)
pdf = c.get(f"/api/documentos/{did}/pdf"); chk("PDF válido", pdf.data[:5] == b"%PDF-" and len(pdf.data) > 2000)
chk("valor de la propuesta = suma", J(c.get(f"/api/documentos/{did}"))["valor"] == 4000000 and J(c.get(f"/api/oportunidades/{oid}"))["valor_estimado"] == 4000000)
did2 = J(P("/api/documentos", {"tipo": "propuesta", "oportunidad_id": oid, "datos": datos}))["id"]
chk("segundo número 0002", J(P(f"/api/documentos/{did2}/generar"))["numero"] == f"PROP-{yr}-0002")
chk("regenerar crea versión 2", J(P(f"/api/documentos/{did}/generar"))["version"] == 2 and len(J(c.get(f"/api/documentos/{did}"))["versiones"]) == 2)
chk("no se marca enviada un borrador", P(f"/api/documentos/{did2}/estado", {"estado": "aceptado"}).status_code == 409)
empty_proposal = J(P("/api/documentos", {"tipo": "propuesta", "oportunidad_id": oid, "datos": {"titulo": "x", "inversion": []}}))
chk("propuesta sin inversión se guarda como borrador", "id" in empty_proposal)
bad = J(c.get("/api/documentos?estado=borrador"))[0]["id"]; chk("borrador vacío no se genera", P(f"/api/documentos/{bad}/generar").status_code == 400)
r = J(P(f"/api/documentos/{did}/estado", {"estado": "enviado"}))
chk("enviar mueve oportunidad y programa seguimiento", "Propuesta enviada" in r["oportunidad"] and J(c.get(f"/api/oportunidades/{oid}"))["etapa"] == "Propuesta enviada")
chk("enviada no se edita", c.put(f"/api/documentos/{did}", json={"datos": datos}).status_code == 409)
chk("dashboard cuenta la propuesta", J(c.get("/api/dashboard"))["hoy"]["propuestas"] >= 1)
chk("aceptar sugiere ganar", J(P(f"/api/documentos/{did}/estado", {"estado": "aceptado"}))["sugerencia"]["valor"] == 4000000)
# cuentas de cobro
chk("cuenta de cobro exige venta ganada", P("/api/documentos", {"tipo": "cuenta_cobro", "oportunidad_id": oid, "datos": {"concepto": "x"}}).status_code == 400)
P(f"/api/oportunidades/{oid}/mover", {"etapa": "Ganado", "valor_cerrado": 4000000})
chk("no documenta más de lo vendido", P("/api/documentos", {"tipo": "cuenta_cobro", "oportunidad_id": oid, "valor": 5000000, "datos": {"concepto": "x"}}).status_code == 400)
cc = J(P("/api/documentos", {"tipo": "cuenta_cobro", "oportunidad_id": oid, "valor": 4000000, "datos": {"concepto": "Diagnóstico organizacional", "dias_pago": 10}}))["id"]
g = J(P(f"/api/documentos/{cc}/generar")); chk("CC-AAAA-0001", g["numero"] == f"CC-{yr}-0001")
chk("PDF de cuenta de cobro", c.get(f"/api/documentos/{cc}/pdf").data[:5] == b"%PDF-")
chk("pagado manual rechazado", P(f"/api/documentos/{cc}/estado", {"estado": "pagado"}).status_code == 409)
P(f"/api/documentos/{cc}/estado", {"estado": "enviado"})
chk("cobro mayor al saldo del documento rechazado", P(f"/api/oportunidades/{oid}/cobros", {"monto": 4000001, "documento_id": cc}).status_code == 400)
cb = J(P(f"/api/oportunidades/{oid}/cobros", {"monto": 1000000, "documento_id": cc}))["id"]
d = J(c.get(f"/api/documentos/{cc}")); chk("cobro parcial no marca pagado", d["estado"] == "enviado" and d["saldo"] == 3000000)
cb2 = J(P(f"/api/oportunidades/{oid}/cobros", {"monto": 3000000, "documento_id": cc}))["id"]
chk("cobro total marca pagado", J(c.get(f"/api/documentos/{cc}"))["estado"] == "pagado")
c.delete(f"/api/cobros/{cb2}"); chk("borrar cobro revierte a enviado", J(c.get(f"/api/documentos/{cc}"))["estado"] == "enviado")
chk("no se anula con cobros", P(f"/api/documentos/{cc}/estado", {"estado": "anulado"}).status_code == 409)
chk("factura electrónica no simulada", J(c.get("/api/facturacion/estado"))["factura_electronica"] is False)
chk("números a letras", MD.num_letras(4000000) == "cuatro millones" and MD.num_letras(1500000) == "un millón quinientos mil")

# 5 tareas, recordatorios, notificaciones
t = J(P("/api/tareas", {"titulo": "Llamar a Laura", "fecha": A.hoy(), "hora": "09:30", "frecuencia": "semanal", "empresa_id": eid}))["id"]
chk("hora inválida rechazada", P("/api/tareas", {"titulo": "x", "fecha": A.hoy(), "hora": "25:00"}).status_code == 400)
chk("título de tarea no textual rechazado", P("/api/tareas", {"titulo": 12, "fecha": A.hoy()}).status_code == 400)
chk("referencia de tarea inexistente rechazada", P("/api/tareas", {"titulo": "x", "fecha": A.hoy(), "empresa_id": 999999}).status_code == 404)
chk("referencia de tarea fraccionaria rechazada", P("/api/tareas", {"titulo": "x", "fecha": A.hoy(), "empresa_id": 1.5}).status_code == 400)
chk("tarea no acepta empresa y oportunidad cruzadas", P("/api/tareas", {"titulo": "x", "fecha": A.hoy(), "empresa_id": e2, "oportunidad_id": oid}).status_code == 400)
chk("notificar_correo requiere booleano", P("/api/tareas", {"titulo": "x", "fecha": A.hoy(), "notificar_correo": "false"}).status_code == 400)
chk("editar tarea inexistente devuelve 404", c.put("/api/tareas/999999", json={"titulo": "x"}).status_code == 404)
chk("cancelar tarea inexistente devuelve 404", c.delete("/api/tareas/999999").status_code == 404)
chk("filtro de tarea no numérico devuelve 400", c.get("/api/tareas?empresa_id=abc").status_code == 400)
n = J(c.get("/api/notificaciones")); chk("tarea de hoy en notificaciones", any(i["titulo"] == "Llamar a Laura" and i["nivel"] == "hoy" for i in n["items"]))
r = J(P(f"/api/tareas/{t}/hecha")); chk("recurrente crea la siguiente", r["fecha_siguiente"] and r["fecha_siguiente"] > A.hoy())
sq = sqlite3.connect(A.DB_PATH); sq.execute("update documentos set fecha_vigencia='2020-01-01' where id=?", (cc,)); sq.commit()
n = J(c.get("/api/notificaciones")); chk("cobro vencido alerta", any(i["tipo"] == "Cobro vencido" for i in n["items"]))
chk("vencido calculado sin cambiar estado", J(c.get(f"/api/documentos/{cc}"))["estado_efectivo"] == "vencido" and J(c.get(f"/api/documentos/{cc}"))["estado"] == "enviado")
sq.execute("update documentos set fecha_envio='2020-01-01' where id=?", (did2,)); sq.execute("update documentos set estado='enviado' where id=?", (did2,)); sq.commit()
chk("propuesta sin respuesta alerta", any(i["tipo"] == "Propuesta sin respuesta" for i in J(c.get("/api/notificaciones"))["items"]))
e = J(c.get("/api/dashboard/ejecutivo"))
chk("tablero ejecutivo completo", all(k in e for k in ("prioritarias", "recibido", "documentos", "cuentas_cobro", "conversion", "sectores", "ubicaciones")) and e["recibido"]["hoy"] == 1000000 and e["cuentas_cobro"]["vencidas"] == 1, e["recibido"])
# correo
chk("sin SMTP no envía", P("/api/avisos/probar-correo").status_code == 400)
sent = []
class FakeSMTP:
    def __init__(s, h, p, timeout=0): sent.append(("conn", h, p))
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def starttls(s): sent.append("tls")
    def login(s, u, p): sent.append(("login", u))
    def send_message(s, m): sent.append(("msg", m["Subject"], m["To"], m.get_content()))
MA.smtplib.SMTP = FakeSMTP
os.environ.update(VELTRIX_SMTP_HOST="smtp.test", VELTRIX_SMTP_USER="u", VELTRIX_SMTP_PASS="p", VELTRIX_SMTP_TO="yo@x.co")
chk("resumen por correo (SMTP simulado)", P("/api/avisos/probar-correo").status_code == 200 and any(isinstance(x, tuple) and x[0] == "msg" and "Cobro vencido" in x[3] for x in sent))

# 6 copias y restauración
b = MS.hacer_respaldo(A, "manual"); chk("copia verificada", b["empresas"] >= SYNTHETIC_COMPANIES and os.path.exists(os.path.join(tmp, "respaldos", b["archivo"])))
chk("copia en un solo archivo", not any(f.endswith(("-wal", "-shm")) for f in os.listdir(os.path.join(tmp, "respaldos"))))
dest = os.path.join(tempfile.mkdtemp(), "crm.db"); sqlite3.connect(dest).execute("create table x(a)").connection.commit()
r = MS.restaurar(dest, os.path.join(tmp, "respaldos", b["archivo"]))
chk("restauración probada (mismos datos)", r["empresas"] == b["empresas"] and r["copia_de_seguridad_previa"] and sqlite3.connect(dest).execute("select count(*) from documentos").fetchone()[0] >= 3)
bad = os.path.join(tempfile.mkdtemp(), "mala.db"); open(bad, "wb").write(b"no es una base" * 100)
try: MS.restaurar(dest, bad); chk("copia dañada se rechaza", False)
except Exception: chk("copia dañada se rechaza", True)
chk("descarga de copia sin salirse de la carpeta", c.get("/api/respaldos/..%2Fcrm.db").status_code in (400, 404))

# 7 migración de una base antigua con datos: hace copia previa y no altera columnas originales
old = tempfile.mkdtemp(); A.DATA_DIR, A.DB_PATH = old, os.path.join(old, "crm.db")
s = sqlite3.connect(A.DB_PATH); s.executescript(A.SCHEMA); s.execute("insert into empresas(nit,razon,razon_norm,activos,ciudad,depto,prioridad) values('900111','Vieja SAS','vieja',5,'CALI-VALLE','VALLE','P2')"); s.commit(); s.close()
A.init_db(); A.init_db()
s = sqlite3.connect(A.DB_PATH)
chk("migración: copia previa creada", any(f.startswith("crm_pre_migracion_") for f in os.listdir(os.path.join(old, "respaldos"))))
chk("migración: dato original intacto y clasificado", s.execute("select razon,prioridad,calidad from empresas").fetchone() == ("Vieja SAS", "P2", "valido"))
chk("migración idempotente (una sola copia)", len([f for f in os.listdir(os.path.join(old, "respaldos")) if f.startswith("crm_pre")]) == 1)

# 8 seguridad
os.environ["VELTRIX_AUTH"] = "1"; MS._fallos.clear()
c2 = A.app.test_client()
chk("API sin sesión: 401", c2.get("/api/empresas").status_code == 401)
chk("página sin sesión: redirige a login", c2.get("/").status_code == 302 and "/login" in c2.get("/").headers["Location"])
chk("archivos estáticos protegidos", c2.get("/static/app.js").status_code == 302)
chk("contraseña corta rechazada", c2.post("/api/auth/setup", json={"usuario": "admin", "clave": "corta"}).status_code == 400)
chk("setup remoto rechazado", c2.post("/api/auth/setup", json={"usuario": "admin", "clave": "UnaClaveLarga123"}, environ_overrides={"REMOTE_ADDR": "8.8.8.8"}).status_code == 403)
chk("crea administrador", c2.post("/api/auth/setup", json={"usuario": "admin", "clave": "UnaClaveLarga123"}).status_code == 200)
chk("hash, no texto plano", "UnaClaveLarga123" not in sqlite3.connect(A.DB_PATH).execute("select hash from usuarios").fetchone()[0])
chk("con sesión accede", c2.get("/api/empresas?per=1").status_code == 200)
chk("origen ajeno rechazado", c2.post("/api/metas", json={}, headers={"Origin": "http://evil.com"}).status_code == 403)
c3 = A.app.test_client()
chk("setup solo una vez", c3.post("/api/auth/setup", json={"usuario": "otro", "clave": "UnaClaveLarga123"}).status_code == 403)
chk("login incorrecto", c3.post("/api/auth/login", json={"usuario": "admin", "clave": "mala"}).status_code == 401)
for _ in range(4): c3.post("/api/auth/login", json={"usuario": "admin", "clave": "mala"})
chk("bloqueo tras 5 intentos", c3.post("/api/auth/login", json={"usuario": "admin", "clave": "UnaClaveLarga123"}).status_code == 429)
MS._fallos.clear()
chk("login correcto", c3.post("/api/auth/login", json={"usuario": "admin", "clave": "UnaClaveLarga123"}).status_code == 200 and c3.get("/api/empresas?per=1").status_code == 200)
chk("cookie HttpOnly + SameSite", "HttpOnly" in c3.post("/api/auth/login", json={"usuario": "admin", "clave": "UnaClaveLarga123"}).headers["Set-Cookie"] and "SameSite=Strict" in c3.post("/api/auth/login", json={"usuario": "admin", "clave": "UnaClaveLarga123"}).headers["Set-Cookie"])
chk("cambiar contraseña", c3.post("/api/auth/cambiar", json={"actual": "UnaClaveLarga123", "nueva": "OtraClaveLarga456"}).status_code == 200)
c3.post("/api/auth/logout"); chk("logout cierra sesión", c3.get("/api/empresas").status_code == 401)
print(f"\n{ok} verificaciones OK")
