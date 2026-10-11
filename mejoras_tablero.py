"""Tablero ejecutivo: separa actividad comercial, ventas cerradas, documentos, cuentas por cobrar y efectivo recibido."""
from datetime import datetime, timedelta

from flask import jsonify

import mejoras_base as MB


def register(app, A):
    @app.get("/api/dashboard/ejecutivo")
    def tablero():
        t = A.hoy()
        d0 = datetime.strptime(t, "%Y-%m-%d")
        lunes = (d0 - timedelta(days=d0.weekday())).strftime("%Y-%m-%d")
        mes = t[:7]
        one = lambda sql, a=(): A.one(sql, a)["n"]
        cob = lambda desde: one("SELECT COALESCE(SUM(monto),0) n FROM cobros WHERE fecha>=? AND fecha<=?", (desde, t))
        notif = A.notificaciones()
        # documentos
        docs = {}
        for tipo in ("propuesta", "cuenta_cobro"):
            docs[tipo] = {e: A.one("SELECT COUNT(*) n, COALESCE(SUM(valor),0) v FROM documentos WHERE tipo=? AND estado=?", (tipo, e)) for e in
                          ("borrador", "generado", "enviado", "aceptado", "rechazado", "pagado")}
        emitidos_mes = A.one("""SELECT COUNT(*) n, COALESCE(SUM(valor),0) v FROM documentos WHERE estado NOT IN ('borrador','anulado')
            AND substr(fecha_emision,1,7)=?""", (mes,))
        # cuentas por cobrar
        vendido = A.one("SELECT COALESCE(SUM(valor_cerrado),0) v FROM oportunidades WHERE etapa='Ganado'")["v"]
        cobrado = one("SELECT COALESCE(SUM(monto),0) n FROM cobros")
        cc = A.rows("""SELECT d.id,d.numero,d.valor,d.fecha_vigencia,e.razon,
            d.valor-COALESCE((SELECT SUM(monto) FROM cobros c WHERE c.documento_id=d.id),0) saldo
            FROM documentos d JOIN empresas e ON e.id=d.empresa_id WHERE d.tipo='cuenta_cobro' AND d.estado IN ('generado','enviado')""")
        venc = [x for x in cc if x["fecha_vigencia"] and x["fecha_vigencia"] < t]
        # conversión entre etapas (oportunidades distintas que alguna vez llegaron a cada etapa)
        orden = A.ETAPAS[:7] + ["Ganado"]
        maxidx = {}
        for r in A.rows("SELECT oportunidad_id oid, etapa FROM historial_etapas"):
            if r["etapa"] in orden:
                maxidx[r["oid"]] = max(maxidx.get(r["oid"], 0), orden.index(r["etapa"]))
        conv, prev = [], None
        for i, e in enumerate(orden):
            n = sum(1 for v in maxidx.values() if v >= i)   # llegó a esta etapa o más allá (aunque haya saltado etapas)
            conv.append({"etapa": e, "llegaron": n, "pct_vs_anterior": round(n / prev * 100) if prev else None})
            prev = n
        # sectores y ubicaciones
        base = """SELECT {k} AS grupo, MIN(e.sector) etiqueta, COUNT(*) opps, SUM(o.etapa='Ganado') ganadas,
            COALESCE(SUM(CASE WHEN o.etapa='Ganado' THEN o.valor_cerrado END),0) vendido,
            COALESCE(SUM((SELECT SUM(monto) FROM cobros c WHERE c.oportunidad_id=o.id)),0) cobrado
            FROM oportunidades o JOIN empresas e ON e.id=o.empresa_id GROUP BY 1 ORDER BY ganadas DESC, vendido DESC, opps DESC LIMIT 8"""
        sectores = A.rows(base.format(k="COALESCE(substr(REPLACE(e.ciiu,'',''),1,3),'s/d')"))
        ubic = A.rows(base.format(k="COALESCE(e.ciudad,'s/d')"))
        return jsonify({
            "prioritarias": [{"id": x["id"], "razon": x["razon"], "ciudad": x["ciudad"], "score": x["score"], "prioridad": x["prioridad"]}
                             for x in MB.prospectar(A, 8)],
            "actividades": notif["conteo"],
            "recibido": {"hoy": cob(t), "semana": cob(lunes), "mes": cob(mes + "-01")},
            "vendido_total": vendido, "cobrado_total": cobrado, "por_cobrar_ventas": vendido - cobrado,
            "documentos": docs, "emitidos_mes": emitidos_mes,
            "cuentas_cobro": {"pendientes": len(cc), "saldo": sum(x["saldo"] for x in cc), "vencidas": len(venc), "saldo_vencido": sum(x["saldo"] for x in venc),
                              "detalle_vencidas": venc[:10]},
            "conversion": conv, "sectores": sectores, "ubicaciones": ubic,
            "nota": "Con pocas oportunidades las tasas y rankings no son concluyentes."})
