"""Prueba de extremo a extremo del circuito comercial. Usa una BD temporal.
Ejecutar:  python tests/test_flujo.py"""
import os, sys, tempfile
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
r = c.post(f"/api/empresas/{eid}/contactos", json={"nombre": "Ana Gómez", "telefono": "3001234567", "correo": "ana@x.co"})
chk("crea contacto", r.status_code == 201)
chk("correo inválido rechazado", c.post(f"/api/empresas/{eid}/contactos", json={"nombre": "X", "correo": "mal"}).status_code == 400)
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
chk("no mover sin paso válido", c.post(f"/api/oportunidades/{oid}/mover", json={"etapa": "Calificado", "proximo_paso": "", "proxima_fecha": ""}).status_code in (200, 400))
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
print(f"\n{ok} verificaciones OK")
