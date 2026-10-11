"""Prueba de extremo a extremo del circuito comercial. Usa una BD temporal.
Ejecutar:  python tests/test_flujo.py"""
import os, sqlite3, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
tmp = tempfile.mkdtemp()
os.environ["VELTRIX_DATA"] = tmp
os.environ["VELTRIX_AUTH"] = "0"   # estas pruebas cubren el circuito comercial; la autenticación se prueba en test_mejoras.py
os.environ["VELTRIX_SEGUNDO_PLANO"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from synthetic_data import build_workbook, SYNTHETIC_COMPANIES

import app as A
c = A.app.test_client()
ok = 0
def chk(name, cond, info=""):
    global ok
    print(("PASA  " if cond else "FALLA ") + name, info if not cond else "")
    if not cond: sys.exit(1)
    ok += 1
def J(r): return r.get_json()

# 1 importar libro sintético creado en memoria
import io
workbook = build_workbook()
r = c.post("/api/importar", data={"archivo": (io.BytesIO(workbook), "empresas_sinteticas.xlsx")}, content_type="multipart/form-data")
j = J(r)
chk("importa empresas sintéticas", r.status_code == 201 and j["nuevas"] == SYNTHETIC_COMPANIES and j["duplicadas"] == 1 and j["errores"] == 1, j)
chk("detecta columnas financieras", {"activos", "pasivos", "patrimonio", "utilidad_op", "nit", "ciiu"} <= set(j["columnas_detectadas"]), j["columnas_detectadas"])
chk("reimportar no duplica", J(c.post("/api/importar", data={"archivo": (io.BytesIO(workbook), "empresas_sinteticas.xlsx")}, content_type="multipart/form-data"))["nuevas"] == 0)
chk("total empresas", J(c.get("/api/empresas?per=1"))["total"] == SYNTHETIC_COMPANIES)
chk("per negativo se limita a una fila", len(J(c.get("/api/empresas?per=-1"))["items"]) == 1)
chk("per excesivo se limita a 200", J(c.get("/api/empresas?per=999"))["per"] == 200)
chk("per no numérico devuelve 400", c.get("/api/empresas?per=abc").status_code == 400)
chk("page no numérico devuelve 400", c.get("/api/empresas?page=abc").status_code == 400)
d = J(c.get("/api/empresas?prioridad=P1&per=1"))["total"]; chk("hay empresas P1", d > 0, d)
chk("búsqueda por NIT sintético", J(c.get("/api/empresas?q=999000001"))["items"][0]["razon"].startswith("Empresa Sintetica 001"))
chk("búsqueda por nombre parcial", J(c.get("/api/empresas?q=sintetica"))["total"] >= 1)
chk("filtro duplicados", J(c.get("/api/empresas?dup=1&per=1"))["total"] > 0)
chk("exportar csv", c.get("/api/empresas/exportar?prioridad=P1").data.count(b"\n") > 1)
chk("reporte import", b"Sin raz" in c.get("/api/importaciones/1/reporte.csv").data)

# 2 empresa nueva + contacto
r = c.post("/api/empresas", json={"razon": "Cliente Prueba SAS", "nit": "900111222", "prioridad": "P1", "ciudad": "Bogotá"})
eid = J(r)["id"]; chk("crea empresa", r.status_code == 201)
chk("NIT repetido rechazado", c.post("/api/empresas", json={"razon": "Otra", "nit": "900111222"}).status_code == 409)
chk("razón obligatoria", c.post("/api/empresas", json={"razon": " "}).status_code == 400)
chk("payload JSON no objeto devuelve 400", c.post("/api/empresas", json=[]).status_code == 400)
before_invalid_contact = J(c.get("/api/empresas?per=1"))["total"]
chk("contacto anidado inválido rechazado sin empresa parcial", c.post("/api/empresas", json={"razon": "No debe persistir", "contacto": {"nombre": 123}}).status_code == 400 and J(c.get("/api/empresas?per=1"))["total"] == before_invalid_contact)
r = c.post(f"/api/empresas/{eid}/contactos", json={"nombre": "Ana Gómez", "telefono": "3001234567", "correo": "ana@x.co"})
cid = J(r)["id"]
chk("crea contacto", r.status_code == 201)
foreign_eid = J(c.post("/api/empresas", json={"razon": "Empresa Contacto Separada", "nit": "900111223"}))["id"]
foreign_cid = J(c.post(f"/api/empresas/{foreign_eid}/contactos", json={"nombre": "Contacto Separado"}))["id"]
before_cross_interaction = sqlite3.connect(A.DB_PATH).execute("SELECT COUNT(*) FROM interacciones").fetchone()[0]
chk("contacto de otra empresa no se asocia", c.post("/api/interacciones", json={"empresa_id": eid, "contacto_id": foreign_cid, "tipo": "Llamada", "resultado": "Intento", "proxima_accion": "Llamar", "proxima_fecha": A.hoy()}).status_code == 404 and sqlite3.connect(A.DB_PATH).execute("SELECT COUNT(*) FROM interacciones").fetchone()[0] == before_cross_interaction)
chk("oportunidad inexistente no se reemplaza silenciosamente", c.post("/api/interacciones", json={"empresa_id": eid, "contacto_id": cid, "oportunidad_id": 999999, "tipo": "Llamada", "resultado": "Intento", "proxima_accion": "Llamar", "proxima_fecha": A.hoy()}).status_code == 404)
chk("resultado de interacción no textual devuelve 400", c.post("/api/interacciones", json={"empresa_id": eid, "tipo": "Llamada", "resultado": 12, "proxima_accion": "Llamar", "proxima_fecha": A.hoy()}).status_code == 400)
chk("correo inválido rechazado", c.post(f"/api/empresas/{eid}/contactos", json={"nombre": "X", "correo": "mal"}).status_code == 400)
chk("contacto no textual rechazado", c.post(f"/api/empresas/{eid}/contactos", json={"nombre": 123}).status_code == 400)
chk("edita contacto existente", c.put(f"/api/contactos/{cid}", json={"nombre": "Ana actualizada", "telefono": "3001234567", "correo": "ana@x.co"}).status_code == 200)
chk("edita contacto inexistente devuelve 404", c.put("/api/contactos/999999", json={}).status_code == 404)
chk("elimina contacto inexistente devuelve 404", c.delete("/api/contactos/999999").status_code == 404)
chk("busca por teléfono", J(c.get("/api/empresas?q=3001234567"))["items"][0]["id"] == eid)
chk("edita empresa", c.put(f"/api/empresas/{eid}", json={"sector": "Software", "prioridad": "P2"}).status_code == 200 and J(c.get(f"/api/empresas/{eid}"))["prioridad"] == "P2")

# 3 seguimiento: primer contacto crea oportunidad "Contactado"
today = A.hoy()
chk("contacto exige próxima acción", c.post("/api/interacciones", json={"empresa_id": eid, "tipo": "Llamada", "resultado": "Sin respuesta"}).status_code == 400)
r = c.post("/api/interacciones", json={"empresa_id": eid, "tipo": "Llamada", "resultado": "Contestó: interesado", "proxima_accion": "Enviar info", "proxima_fecha": today})
rj = J(r); oid = rj["oportunidad_id"]
chk("registra primer contacto", r.status_code == 201 and rj["primer_contacto"])
o = J(c.get(f"/api/oportunidades/{oid}")); chk("opp creada en Contactado", o["etapa"] == "Contactado" and o["proximo_paso"] == "Enviar info")
r = c.post("/api/interacciones", json={"empresa_id": eid, "tipo": "Correo", "resultado": "Contestó: pidió información", "proxima_accion": "Llamar", "proxima_fecha": A.hoy()})
chk("segundo contacto es seguimiento", not J(r)["primer_contacto"])

# 4 pipeline
move_keep_step = c.post(f"/api/oportunidades/{oid}/mover", json={"etapa": "Calificado", "proximo_paso": "", "proxima_fecha": ""})
chk("mover conserva la próxima acción previa si se omite", move_keep_step.status_code == 200 and J(c.get(f"/api/oportunidades/{oid}"))["proximo_paso"] == "Llamar")
for et in ["Conversación iniciada", "Calificado", "Reunión agendada", "Propuesta enviada", "Negociación"]:
    r = c.post(f"/api/oportunidades/{oid}/mover", json={"etapa": et}); chk("mueve a " + et, r.status_code == 200, J(r))
c.put(f"/api/oportunidades/{oid}", json={"valor_estimado": 5000000, "responsable": "Laura"})
chk("no se puede borrar paso de opp activa", c.put(f"/api/oportunidades/{oid}", json={"proximo_paso": "", "proxima_fecha": today}).status_code == 400)
chk("cobro antes de ganar rechazado", c.post(f"/api/oportunidades/{oid}/cobros", json={"monto": 1}).status_code == 400)
_e = J(c.post("/api/empresas", json={"razon": "Sin Valor SAS"}))["id"]
_o = J(c.post("/api/oportunidades", json={"empresa_id": _e}))["id"]
chk("ganar sin valor (ni estimado) rechazado", c.post(f"/api/oportunidades/{_o}/mover", json={"etapa": "Ganado"}).status_code == 400)
chk("perder sin motivo permitido", c.post(f"/api/oportunidades/{_o}/mover", json={"etapa": "Perdido"}).status_code == 200)
r = c.post(f"/api/oportunidades/{oid}/mover", json={"etapa": "Ganado", "valor_cerrado": 4000000}); chk("gana 4.000.000", r.status_code == 200)
# 5 cobros
chk("cobro parcial", c.post(f"/api/oportunidades/{oid}/cobros", json={"monto": 1000000}).status_code == 201)
chk("sobrecobro rechazado", c.post(f"/api/oportunidades/{oid}/cobros", json={"monto": 3500000}).status_code == 400)
chk("cobro restante", c.post(f"/api/oportunidades/{oid}/cobros", json={"monto": 3000000}).status_code == 201)
o = J(c.get(f"/api/oportunidades/{oid}")); chk("saldo 0", o["saldo"] == 0 and o["cobrado"] == 4000000)

# 6 dashboard
d = J(c.get("/api/dashboard")); h = d["hoy"]
chk("dashboard contactos nuevos=1", h["contactos_nuevos"] == 1, h)
chk("dashboard seguimientos=1", h["seguimientos"] == 1, h)
chk("dashboard propuestas/reuniones=1", h["propuestas"] == 1 and h["reuniones"] == 1, h)
chk("vendido ≠ cobrado y cobrado=4M", h["vendido"] == 4000000 and h["cobrado"] == 4000000 and d["ganadas"]["por_cobrar"] == 0)
chk("metas por defecto", d["metas"]["contactos_nuevos"] == 20 and d["metas"]["ventas_cerradas"] == 1000000 and d["metas"]["cobro_diario"] == 1000000)
chk("edita metas", c.put("/api/metas", json={"propuestas": 5}).status_code == 200 and J(c.get("/api/metas"))["propuestas"] == 5)

# 7 HOY, vencidos, sin paso
e2 = J(c.post("/api/empresas", json={"razon": "Segunda Ltda", "nit": "900333444", "prioridad": "P1"}))["id"]
o2 = J(c.post("/api/oportunidades", json={"empresa_id": e2, "proximo_paso": "Llamar", "proxima_fecha": "2020-01-01", "valor_estimado": 1000}))["id"]
chk("opp duplicada activa rechazada", c.post("/api/oportunidades", json={"empresa_id": e2}).status_code == 409)
hy = J(c.get("/api/hoy"))
chk("HOY: vencido aparece", any(x["id"] == o2 for x in hy["vencidos"] + hy["por_contactar"] + hy["calientes"]))
chk("HOY: sugiere nuevas empresas", len(hy["nuevas"]) >= 5 and all(x["prioridad"] in ("P1", "P2", "P3") for x in hy["nuevas"]))
chk("seguimientos vencidos", any(x["id"] == o2 for x in J(c.get("/api/seguimientos?vista=vencidos"))))
r = c.post(f"/api/oportunidades/{o2}/mover", json={"etapa": "Perdido", "motivo": "Precio"}); chk("marca perdida", r.status_code == 200)
chk("borrar empresa con historial bloqueado", c.delete(f"/api/empresas/{eid}").status_code == 409)
chk("importar archivo inválido", c.post("/api/importar", data={"archivo": (__import__("io").BytesIO(b"x"), "a.pdf")}, content_type="multipart/form-data").status_code == 400)
# CSV propio con contactos
import io
csv = "Empresa,NIT,Teléfono,Correo,Contacto,Ciudad\nAcme SAS,800555666,3109998888,a@acme.co,Luis Ruiz,Cali\nAcme SAS,800555666,,,,\nBeta,,3000000000,,,Medellín\n,1,,,,\n"
r = c.post("/api/importar", data={"archivo": (io.BytesIO(csv.encode()), "mi.csv")}, content_type="multipart/form-data"); j = J(r)
chk("importa CSV con contactos", j["nuevas"] == 2 and j["duplicadas"] == 1 and j["errores"] == 1, j)
chk("contacto importado", J(c.get("/api/empresas?q=3109998888"))["total"] == 1)

race_eid = J(c.post("/api/empresas", json={"razon": "Empresa Carrera Oportunidad", "nit": "900888001"}))["id"]
def crear_oportunidad_paralela(_):
    with A.app.test_client() as client:
        return client.post("/api/oportunidades", json={"empresa_id": race_eid}).status_code
with ThreadPoolExecutor(max_workers=6) as executor:
    race_statuses = list(executor.map(crear_oportunidad_paralela, range(6)))
chk("creación concurrente deja solo una oportunidad activa", race_statuses.count(201) == 1 and race_statuses.count(409) == 5, race_statuses)


parallel_import = b"Empresa,NIT\nEmpresa Importacion Paralela,900888003\n"
def importar_paralelo(_):
    with A.app.test_client() as client:
        return client.post("/api/importar", data={"archivo": (io.BytesIO(parallel_import), "paralela.csv")}, content_type="multipart/form-data")
with ThreadPoolExecutor(max_workers=2) as executor:
    import_responses = list(executor.map(importar_paralelo, range(2)))
import_totals = [response.get_json()["nuevas"] for response in import_responses]
chk("importaciones concurrentes no duplican NIT", all(response.status_code == 201 for response in import_responses) and sorted(import_totals) == [0, 1], import_totals)
cash_eid = J(c.post("/api/empresas", json={"razon": "Empresa Carrera Recaudo", "nit": "900888002"}))["id"]
cash_oid = J(c.post("/api/oportunidades", json={"empresa_id": cash_eid, "valor_estimado": 1000}))["id"]
chk("prepara venta de prueba para concurrencia", c.post(f"/api/oportunidades/{cash_oid}/mover", json={"etapa": "Ganado", "valor_cerrado": 1000, "fecha_cierre": "2020-01-01"}).status_code == 200)
def registrar_cobro_paralelo(_):
    with A.app.test_client() as client:
        return client.post(f"/api/oportunidades/{cash_oid}/cobros", json={"monto": 600, "fecha": "2020-01-01"}).status_code
with ThreadPoolExecutor(max_workers=2) as executor:
    cash_statuses = list(executor.map(registrar_cobro_paralelo, range(2)))
cash_total = sqlite3.connect(A.DB_PATH).execute("SELECT COALESCE(SUM(monto),0) FROM cobros WHERE oportunidad_id=?", (cash_oid,)).fetchone()[0]
chk("cobros concurrentes no exceden el valor cerrado", sorted(cash_statuses) == [201, 400] and cash_total == 600, (cash_statuses, cash_total))

def crear_empresa_paralela(_):
    with A.app.test_client() as client:
        return client.post("/api/empresas", json={"razon": "Empresa NIT Paralela", "nit": "900888004"}).status_code
with ThreadPoolExecutor(max_workers=2) as executor:
    company_statuses = list(executor.map(crear_empresa_paralela, range(2)))
chk("empresas concurrentes no duplican NIT", sorted(company_statuses) == [201, 409], company_statuses)

print(f"\n{ok} verificaciones OK")
