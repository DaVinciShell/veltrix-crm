"""Agenda ejecutiva y reporte diario calculados desde métricas del CRM."""
from flask import jsonify


def _repartir(total, partes):
    base, resto = divmod(max(int(total), 0), partes)
    return [base + (1 if i < resto else 0) for i in range(partes)]


def _cop(valor):
    return "$" + f"{round(valor):,}".replace(",", ".")


def register(app, A):
    def construir_reporte(fecha):
        tablero = A.dashboard_data(fecha)
        hoy = tablero["hoy"]
        metas = {r["clave"]: r["valor"] for r in A.rows("SELECT * FROM metas")}
        meta_ventas = float(metas.get("ventas_cerradas", 1_000_000))
        meta_cobros = float(metas.get("cobro_diario", 1_000_000))
        vencidos = A.one("""SELECT COUNT(*) n FROM oportunidades
            WHERE etapa NOT IN ('Ganado','Perdido') AND proxima_fecha<?""", (fecha,))["n"]
        sin_paso = A.one("""SELECT COUNT(*) n FROM oportunidades WHERE etapa NOT IN ('Ganado','Perdido')
            AND (proximo_paso IS NULL OR proximo_paso='' OR proxima_fecha IS NULL)""")["n"]
        ventas = float(hoy["vendido"])
        cobrado = float(hoy["cobrado"])
        reporte = {
            "fecha": fecha,
            "actividad": {k: hoy[k] for k in ("contactos_nuevos", "seguimientos", "propuestas", "reuniones")},
            "ventas_cerradas": {"cop": ventas, "meta_cop": meta_ventas, "faltante_cop": max(meta_ventas - ventas, 0)},
            "dinero_cobrado": {"cop": cobrado, "meta_cop": meta_cobros, "faltante_cop": max(meta_cobros - cobrado, 0)},
            "pipeline": tablero["activas"],
            "pendientes": {"seguimientos_vencidos": vencidos, "sin_proximo_paso": sin_paso},
        }
        reporte["texto"] = "\n".join([
            f"Reporte ejecutivo VELTRIX · {fecha}",
            f"Ventas cerradas: {_cop(ventas)} / {_cop(meta_ventas)} COP",
            f"Dinero cobrado: {_cop(cobrado)} / {_cop(meta_cobros)} COP",
            f"Contactos nuevos: {hoy['contactos_nuevos']} · Seguimientos: {hoy['seguimientos']}",
            f"Propuestas: {hoy['propuestas']} · Reuniones: {hoy['reuniones']}",
            f"Pipeline activo: {tablero['activas']['n']} oportunidades · estimado {_cop(tablero['activas']['valor'])} COP",
            f"Seguimientos vencidos: {vencidos} · Oportunidades sin próximo paso: {sin_paso}",
        ])
        return reporte, metas, vencidos

    def objetivo(nombre, metrica, meta, avance=None, unidad=""):
        dato = {"nombre": nombre, "metrica": metrica, "meta": meta, "unidad": unidad}
        if avance is not None:
            dato["avance_dia"] = avance
        return dato

    def construir_agenda(fecha):
        reporte, metas, vencidos = construir_reporte(fecha)
        h = A.contar_dia(fecha)
        contactos = _repartir(metas.get("contactos_nuevos", 20), 2)
        seguimientos = _repartir(metas.get("seguimientos", 10), 2)
        propuestas = _repartir(metas.get("propuestas", 3), 3)
        reuniones = _repartir(metas.get("reuniones", 3), 3)
        compromisos_hoy = [x for x in A.notificaciones()["items"] if x["fecha"] == fecha]
        bloques = [
            {"inicio": "08:00", "fin": "09:30", "nombre": "Priorizar", "objetivos": [
                objetivo("Contactar empresas nuevas", "contactos_nuevos", contactos[0], h["contactos_nuevos"], "empresas"),
                objetivo("Enviar propuestas", "propuestas", propuestas[0], h["propuestas"], "propuestas"),
                objetivo("Revisar seguimientos vencidos", "seguimientos_vencidos", vencidos, unidad="pendientes"),
            ]},
            {"inicio": "09:30", "fin": "12:00", "nombre": "Prospectar y conversar", "objetivos": [
                objetivo("Contactar empresas nuevas", "contactos_nuevos", contactos[1], h["contactos_nuevos"], "empresas"),
                objetivo("Completar seguimientos", "seguimientos", seguimientos[0], h["seguimientos"], "acciones"),
                objetivo("Agendar reuniones", "reuniones", reuniones[0], h["reuniones"], "reuniones"),
            ]},
            {"inicio": "13:00", "fin": "15:00", "nombre": "Avanzar oportunidades", "objetivos": [
                objetivo("Completar seguimientos", "seguimientos", seguimientos[1], h["seguimientos"], "acciones"),
                objetivo("Enviar propuestas", "propuestas", propuestas[1], h["propuestas"], "propuestas"),
                objetivo("Agendar reuniones", "reuniones", reuniones[1], h["reuniones"], "reuniones"),
            ]},
            {"inicio": "15:00", "fin": "17:00", "nombre": "Cerrar y registrar", "objetivos": [
                objetivo("Cerrar ventas", "ventas_cerradas", metas.get("ventas_cerradas", 1_000_000), h["vendido"], "COP"),
                objetivo("Registrar dinero cobrado", "dinero_cobrado", metas.get("cobro_diario", 1_000_000), h["cobrado"], "COP"),
                objetivo("Agendar reuniones", "reuniones", reuniones[2], h["reuniones"], "reuniones"),
            ]},
        ]
        sin_horario = []
        for item in compromisos_hoy:
            hora = item.get("hora")
            bloque = next((b for b in bloques if hora and b["inicio"] <= hora < b["fin"]), None)
            compromiso = {k: item[k] for k in ("titulo", "detalle", "tipo", "fecha", "hora", "link")}
            if bloque:
                bloque.setdefault("compromisos", []).append(compromiso)
            else:
                sin_horario.append(compromiso)
        reporte["compromisos_hoy"] = len(compromisos_hoy)
        return {"fecha": fecha, "bloques": bloques, "sin_horario": sin_horario, "reporte": reporte,
                "nota": "La agenda propone metas por bloque; el CRM mide el avance diario, no el cumplimiento por hora."}

    A.construir_agenda = construir_agenda

    @app.get("/api/agenda/ejecutiva")
    def api_agenda_ejecutiva():
        return jsonify(construir_agenda(A.hoy()))

    @app.get("/api/reportes/ejecutivo")
    def api_reporte_ejecutivo():
        fecha = A.request.args.get("fecha") or A.hoy()
        if not A.valid_date(fecha):
            raise A.ApiError("Fecha no válida.")
        reporte, _, _ = construir_reporte(fecha)
        return jsonify(reporte)