"""Estado y contexto agregado para una futura IA; no contiene cliente HTTP."""
import os

from flask import jsonify


def estado():
    return {
        "proveedor": "OpenAI",
        "api_key_configurada": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        "activacion_solicitada": os.environ.get("VELTRIX_OPENAI_ENABLED") == "1",
        "integracion": "no_conectada",
        "llamadas_externas": False,
        "modo": "solo_lectura_agregada",
        "modelo_configurado": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    }


def contexto_agregado(A):
    fecha = A.hoy()
    dashboard = A.dashboard_data(fecha)
    hoy = dashboard["hoy"]
    metas = dashboard["metas"]
    return {
        "fecha": fecha,
        "actividad_hoy": {k: hoy[k] for k in ("contactos_nuevos", "seguimientos", "propuestas", "reuniones")},
        "ventas_cerradas_hoy_cop": hoy["vendido"],
        "dinero_cobrado_hoy_cop": hoy["cobrado"],
        "pipeline_activo": dashboard["activas"],
        "metas_cop": {
            "ventas_cerradas": metas.get("ventas_cerradas", 1_000_000),
            "dinero_cobrado": metas.get("cobro_diario", 1_000_000),
        },
        "pendientes": {"seguimientos_vencidos": dashboard["vencidos"], "sin_proximo_paso": dashboard["sin_paso"]},
    }


def register(app, A):
    @app.get("/api/ia/estado")
    def api_ia_estado():
        return jsonify(estado())

    @app.get("/api/ia/contexto")
    def api_ia_contexto():
        return jsonify(contexto_agregado(A))