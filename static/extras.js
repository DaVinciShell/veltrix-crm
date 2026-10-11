/* VELTRIX CRM · mejoras: calidad, puntaje, documentos, tareas, mensajes, configuración, tablero ejecutivo */
const lines = t => (t || '').split('\n').map(x => x.trim()).filter(Boolean);
const num = v => Number(String(v || '').replace(/[^\d.-]/g, '')) || 0;
const stTag = s => `<span class="st ${s}">${s}</span>`;
const nivelTxt = { vencido: 'Vencido', hoy: 'Hoy', proximo: 'Próximos 7 días' };
window.EXTRA_ROUTES = { agenda: vAgenda, calidad: vCalidad, documentos: vDocs, tareas: vTareas, config: vConfig };

/* ---------- agenda y reporte ejecutivo (solo datos agregados) ---------- */
async function vAgenda() {
  const d = await api('/api/agenda/ejecutiva'); const r = d.reporte;
  const cop = n => COP(n || 0);
  const unidad = (n, u) => {
    const singular = { empresas: 'empresa', propuestas: 'propuesta', pendientes: 'pendiente', acciones: 'acción', reuniones: 'reunión' };
    return `${NUM(n)} ${n === 1 ? (singular[u] || u) : u}`;
  };
  const kpi = (titulo, actual, meta) => `<div class="card kpi"><div class="l">${titulo}</div><div class="v">${cop(actual)}</div><div class="sm mut">Meta diaria ${cop(meta)}</div></div>`;
  const bloques = d.bloques.map(b => `<div class="card"><div class="row sp"><h2>${esc(b.nombre)}</h2><span class="tag">${b.inicio}–${b.fin}</span></div>
    ${b.objetivos.map(o => `<div class="item"><div class="main"><b>${esc(o.nombre)}</b><div class="sm mut">Meta del bloque: ${o.unidad === 'COP' ? cop(o.meta) : esc(unidad(o.meta, o.unidad))}${o.avance_dia != null ? ` · avance diario ${o.unidad === 'COP' ? cop(o.avance_dia) : esc(unidad(o.avance_dia, o.unidad))}` : ''}</div></div></div>`).join('')}
    ${(b.compromisos || []).map(k => `<div class="item"><div class="main"><a href="${esc(k.link)}"><b>${esc(k.titulo)}</b></a><div class="sm mut">${esc(k.tipo)}${k.hora ? ' · ' + esc(k.hora) : ''} · ${esc(k.detalle)}</div></div></div>`).join('')}
  </div>`).join('');
  view.innerHTML = `<div class="row sp"><h1>Agenda ejecutiva · ${fmtF(d.fecha)}</h1></div>
  <div class="grid g4">${kpi('Ventas cerradas', r.ventas_cerradas.cop, r.ventas_cerradas.meta_cop)}${kpi('Dinero cobrado', r.dinero_cobrado.cop, r.dinero_cobrado.meta_cop)}
    <div class="card kpi"><div class="l">Contactos nuevos</div><div class="v">${NUM(r.actividad.contactos_nuevos)}</div><div class="sm mut">Seguimientos ${NUM(r.actividad.seguimientos)}</div></div>
    <div class="card kpi"><div class="l">Seguimientos vencidos</div><div class="v">${NUM(r.pendientes.seguimientos_vencidos)}</div><div class="sm mut">Sin próximo paso ${NUM(r.pendientes.sin_proximo_paso)}</div></div></div>
  <div class="grid g2" style="margin-top:12px">${bloques}</div>
  ${d.sin_horario.length ? `<div class="card"><h2>Compromisos sin hora asignada</h2>${d.sin_horario.map(k => `<div class="item"><div class="main"><a href="${esc(k.link)}"><b>${esc(k.titulo)}</b></a><div class="sm mut">${esc(k.tipo)} · ${esc(k.detalle)}</div></div></div>`).join('')}</div>` : ''}
  <div class="card"><h2>Reporte ejecutivo</h2><div class="sm" style="white-space:pre-line">${esc(r.texto)}</div></div>
  <div class="sm mut">${esc(d.nota)}</div>`;
}

/* ---------- campana de avisos (solo suena con el CRM abierto) ---------- */
async function pulso() {
  try {
    const n = await api('/api/notificaciones'); const c = n.conteo; const k = c.vencido + c.hoy;
    $('#bn').textContent = k; $('#bell').classList.toggle('zero', !k);
    if ('Notification' in window && Notification.permission === 'granted') {
      const vistos = JSON.parse(localStorage.getItem('vx_avisados') || '{}'); const hoy = n.fecha; const ahora = new Date().toTimeString().slice(0, 5);
      if (vistos._d !== hoy) { for (const k2 in vistos) delete vistos[k2]; vistos._d = hoy; }
      n.items.filter(i => i.nivel !== 'proximo' && (!i.hora || i.hora <= ahora) && !vistos[i.key]).slice(0, 5)
        .forEach(i => { new Notification('VELTRIX · ' + i.tipo, { body: `${i.titulo} — ${i.detalle}` }); vistos[i.key] = 1; });
      localStorage.setItem('vx_avisados', JSON.stringify(vistos));
    }
  } catch (e) { }
}
setInterval(pulso, 60000); window.addEventListener('load', () => setTimeout(pulso, 800));

/* ---------- mensajes (WhatsApp / correo): se prepara, usted envía ---------- */
const PLS = [['primer_contacto', 'Primer contacto'], ['seguimiento', 'Seguimiento'], ['envio_propuesta', 'Envío de propuesta'], ['recuperacion', 'Recuperar oportunidad'], ['recordatorio_cobro', 'Recordatorio de cobro']];
window.mensaje = guard(async (eid, plantilla = 'primer_contacto', canal = 'whatsapp', docId) => {
  const p = await api(`/api/mensajes/preparar?empresa_id=${eid}&plantilla=${plantilla}&canal=${canal}${docId ? '&documento_id=' + docId : ''}`);
  const waBase = p.wa_url ? p.wa_url.split('?text=')[0] : null, mBase = p.mailto_url ? p.mailto_url.split('?subject=')[0] : null;
  modal('Mensaje preparado', `<div class="chips" id="cn">${['whatsapp', 'correo'].map(c => `<button type="button" data-c="${c}" class="${c === canal ? 'on' : ''}">${c === 'whatsapp' ? 'WhatsApp' : 'Correo'}</button>`).join('')}</div>
   <label>Plantilla</label><select id="pl">${PLS.map(([k, l]) => `<option value="${k}" ${k === plantilla ? 'selected' : ''}>${l}</option>`).join('')}</select>
   ${canal === 'correo' ? `<label>Asunto</label><input id="as" style="width:100%" value="${esc(p.asunto)}">` : ''}
   <label>Mensaje (editable)</label><textarea id="cu" class="ta" style="min-height:170px;font-family:inherit;font-size:13px">${esc(p.cuerpo)}</textarea>
   ${p.pendientes.length ? `<div class="note" style="margin-top:8px">Complete antes de enviar: ${p.pendientes.map(esc).join(', ')} (datos del emisor en Configuración).</div>` : ''}
   ${p.aviso ? `<div class="note" style="margin-top:8px">${esc(p.aviso)}</div>` : ''}
   <div class="sm mut" style="margin-top:6px">${p.contacto ? 'Contacto: ' + esc(p.contacto) + ' · ' : ''}${p.telefono ? esc(p.telefono) : 'sin teléfono'} · ${p.correo ? esc(p.correo) : 'sin correo'}. No se envía nada automáticamente.</div>
   <div class="row" style="margin-top:12px"><button class="btn" id="bc">Copiar</button>
   ${canal === 'whatsapp' ? `<a class="btn acc ${waBase ? '' : 'disabled'}" id="bw" ${waBase ? 'target="_blank" rel="noopener"' : 'style="opacity:.4;pointer-events:none"'}>Abrir WhatsApp</a>` : `<a class="btn acc" id="bm" ${mBase ? '' : 'style="opacity:.4;pointer-events:none"'}>Abrir correo</a>`}
   <button class="btn primary" id="br">Ya lo envié → registrar contacto</button></div>`, m => {
    $('#cn', m).onclick = e => { const b = e.target.closest('button'); if (b) mensaje(eid, $('#pl', m).value, b.dataset.c, docId); };
    $('#pl', m).onchange = () => mensaje(eid, $('#pl', m).value, canal, docId);
    const txt = () => encodeURIComponent($('#cu', m).value);
    if (waBase) $('#bw', m).onclick = () => { $('#bw', m).href = waBase + '?text=' + txt(); };
    if (mBase) $('#bm', m).onclick = () => { $('#bm', m).href = mBase + '?subject=' + encodeURIComponent($('#as', m).value) + '&body=' + txt(); };
    $('#bc', m).onclick = async () => { await navigator.clipboard.writeText($('#cu', m).value); toast('Mensaje copiado'); };
    $('#br', m).onclick = () => { closeModal(); regContacto(eid); };
  });
});

/* ---------- tareas ---------- */
window.nuevaTarea = (eid, oid, docId) => modal('Nueva tarea / recordatorio', `<form id="ft"><label>Título *</label><input name="titulo" style="width:100%" required>
  <div class="f2"><div><label>Tipo</label><select name="tipo">${['Llamada', 'Correo', 'Reunión', 'Propuesta', 'Cobro', 'Otro'].map(x => `<option>${x}</option>`).join('')}</select></div>
  <div><label>Frecuencia</label><select name="frecuencia"><option value="una_vez">Una vez</option><option value="diaria">Diaria</option><option value="semanal">Semanal</option><option value="mensual">Mensual</option></select></div>
  <div><label>Fecha *</label><input type="date" name="fecha" value="${addDias(1)}"></div><div><label>Hora</label><input type="time" name="hora"></div></div>${fechaChips('fecha')}
  <label><input type="checkbox" name="notificar_correo" style="width:auto"> Incluir en el resumen por correo</label>
  <div class="row" style="margin-top:14px"><button class="btn primary">Guardar</button></div></form>`, m => {
  wireChips(m);
  $('#ft', m).onsubmit = guard(async ev => { ev.preventDefault(); const b = formData(m); if (eid) b.empresa_id = eid; if (oid) b.oportunidad_id = oid; if (docId) b.documento_id = docId;
    await api('/api/tareas', { method: 'POST', body: b }); closeModal(); toast('Tarea creada'); route(); });
});
async function vTareas() {
  const [n, ts, es] = await Promise.all([api('/api/notificaciones'), api('/api/tareas'), api('/api/avisos/estado')]);
  const g = lv => n.items.filter(i => i.nivel === lv);
  view.innerHTML = `<div class="row sp"><h1>Tareas y avisos</h1><button class="btn primary" onclick="nuevaTarea()">+ Tarea</button></div>
  <div class="note">${esc(es.nota)} ${es.correo_configurado ? 'Resumen por correo: activo → ' + esc(es.destino) : 'Resumen por correo: no configurado.'}</div>
  <div class="grid g2"><div>${['vencido', 'hoy', 'proximo'].map(lv => `<div class="card nv ${lv}"><h2>${nivelTxt[lv]} <span class="tag ${lv === 'vencido' ? 'red' : ''}">${g(lv).length}</span></h2>
   ${g(lv).length ? g(lv).map(i => `<div class="item"><div class="main"><a class="t" href="${i.link}">${esc(i.titulo)}</a> <span class="tag">${esc(i.tipo)}</span><div class="sm mut">${esc(i.detalle)} · ${fmtF(i.fecha)}${i.hora ? ' ' + i.hora : ''}</div></div></div>`).join('') : '<div class="sm mut">Nada.</div>'}</div>`).join('')}</div>
  <div><div class="card"><h2>Mis tareas pendientes <span class="tag">${ts.length}</span></h2>${ts.length ? ts.map(k => `<div class="item"><div class="main"><b>${esc(k.titulo)}</b> <span class="tag">${esc(k.tipo || '')}</span>${k.frecuencia !== 'una_vez' ? ` <span class="tag amb">${k.frecuencia}</span>` : ''}<div class="sm mut">${fmtF(k.fecha)}${k.hora ? ' ' + k.hora : ''}${k.razon ? ' · ' + esc(k.razon) : ''}</div></div>
   <div class="acts"><button class="btn sm primary" onclick="tareaHecha(${k.id})">Hecha</button><button class="btn sm danger" onclick="tareaCancelar(${k.id})">×</button></div></div>`).join('') : '<div class="sm mut">Sin tareas. Los seguimientos de oportunidades aparecen automáticamente a la izquierda.</div>'}</div></div></div>`;
}
window.tareaHecha = guard(async id => { const r = await api(`/api/tareas/${id}/hecha`, { method: 'POST' }); toast(r.fecha_siguiente ? 'Hecha · próxima: ' + r.fecha_siguiente : 'Tarea completada'); route(); pulso(); });
window.tareaCancelar = guard(async id => { await api('/api/tareas/' + id, { method: 'DELETE' }); route(); pulso(); });

/* ---------- documentos ---------- */
const campoTxt = (n, l, v, rows = 3, ph = '') => `<label>${l}</label><textarea name="${n}" class="ta" rows="${rows}" placeholder="${esc(ph)}" style="min-height:${rows * 22}px">${esc(v || '')}</textarea>`;
window.docPropuesta = guard(async (eid, did) => {
  let d = null, em = await api('/api/config/emisor'), e = await api('/api/empresas/' + eid);
  if (did) d = (await api('/api/documentos/' + did)).datos;
  const opp = e.oportunidades.find(o => !['Ganado', 'Perdido'].includes(o.etapa));
  if (!opp && !did) { toast('Primero agregue la empresa al pipeline (+ Pipeline).', true); return; }
  d = d || {};
  modal((did ? 'Editar' : 'Nueva') + ' propuesta · ' + e.razon, `<form id="fd"><label>Título *</label><input name="titulo" style="width:100%" value="${esc(d.titulo || 'Propuesta de consultoría para ' + e.razon)}">
   <div class="f2"><div><label>Atención a</label><input name="contacto" value="${esc(d.contacto || (e.contactos[0] && e.contactos[0].nombre) || '')}"></div><div><label>Vigencia (días)</label><input name="vigencia_dias" value="${d.vigencia_dias || em.vigencia_dias}"></div></div>
   ${campoTxt('necesidad', 'Necesidad identificada (lo que el cliente le dijo)', d.necesidad, 3)}
   ${campoTxt('alcance', 'Alcance (uno por línea)', (d.alcance || []).join('\n'), 4)}
   ${campoTxt('entregables', 'Entregables (uno por línea)', (d.entregables || []).join('\n'), 3)}
   ${campoTxt('cronograma', 'Cronograma (fase | duración)', (d.cronograma || []).map(c => c.fase + ' | ' + c.duracion).join('\n'), 3, 'Diagnóstico | 2 semanas')}
   ${campoTxt('inversion', 'Inversión (concepto | valor COP) *', (d.inversion || []).map(i => i.concepto + ' | ' + i.valor).join('\n'), 3, 'Diagnóstico organizacional | 3000000')}
   <div class="f2"><div><label>IVA % (0 si no aplica)</label><input name="iva_pct" value="${d.iva_pct || 0}"></div><div><label>Forma de pago</label><input name="forma_pago" value="${esc(d.forma_pago || '50% al iniciar, 50% al entregar')}"></div></div>
   ${campoTxt('condiciones', 'Condiciones', d.condiciones || em.condiciones, 3)}
   <div class="row" style="margin-top:14px"><button class="btn" type="button" id="gb">Guardar borrador</button><button class="btn primary" type="button" id="gg">Guardar y generar PDF</button></div></form>`, m => {
    const leer = () => { const b = formData(m); return { titulo: b.titulo, contacto: b.contacto, vigencia_dias: num(b.vigencia_dias), necesidad: b.necesidad, alcance: lines(b.alcance), entregables: lines(b.entregables),
      cronograma: lines(b.cronograma).map(l => { const [f, du] = l.split('|'); return { fase: f.trim(), duracion: (du || '').trim() }; }),
      inversion: lines(b.inversion).map(l => { const [c, v] = l.split('|'); return { concepto: c.trim(), valor: num(v) }; }), iva_pct: num(b.iva_pct), forma_pago: b.forma_pago, condiciones: b.condiciones }; };
    const guardar = gen => guard(async () => { const datos = leer(); let id = did;
      if (did) await api('/api/documentos/' + did, { method: 'PUT', body: { datos } }); else id = (await api('/api/documentos', { method: 'POST', body: { tipo: 'propuesta', empresa_id: eid, oportunidad_id: opp && opp.id, datos } })).id;
      if (gen) { const r = await api(`/api/documentos/${id}/generar`, { method: 'POST' }); toast('Generada ' + r.numero); location.href = `/api/documentos/${id}/pdf`; } else toast('Borrador guardado');
      closeModal(); location.hash = 'documentos'; route(); });
    $('#gb', m).onclick = guardar(false); $('#gg', m).onclick = guardar(true);
  });
});
window.docCuenta = guard(async oid => {
  const o = await api('/api/oportunidades/' + oid), docs = await api(`/api/documentos?oportunidad_id=${oid}`);
  const doc = docs.filter(d => d.tipo === 'cuenta_cobro' && d.estado !== 'anulado').reduce((a, d) => a + d.valor, 0), prop = docs.find(d => d.tipo === 'propuesta');
  const falta = (o.valor_cerrado || 0) - doc; if (falta <= 0) { toast('Esta venta ya está totalmente documentada.', true); return; }
  modal('Cuenta de cobro · ' + o.razon, `<div class="sm">Vendido ${COP(o.valor_cerrado)} · ya documentado ${COP(doc)} · <b>por documentar ${COP(falta)}</b></div>
   <form id="fd"><label>Concepto *</label><input name="concepto" style="width:100%" value="${esc(prop ? prop.titulo : 'Servicios de consultoría')}"><div class="f2"><div><label>Valor (COP) *</label><input name="valor" value="${Math.round(falta)}"></div><div><label>Días para pagar</label><input name="dias_pago" value="15"></div></div>
   <div class="note" style="margin-top:10px">Es una cuenta de cobro, no una factura electrónica.</div>
   <div class="row" style="margin-top:12px"><button class="btn primary">Crear y generar PDF</button></div></form>`, m => {
    $('#fd', m).onsubmit = guard(async ev => { ev.preventDefault(); const b = formData(m);
      const id = (await api('/api/documentos', { method: 'POST', body: { tipo: 'cuenta_cobro', oportunidad_id: oid, valor: num(b.valor), datos: { concepto: b.concepto, dias_pago: num(b.dias_pago) } } })).id;
      const r = await api(`/api/documentos/${id}/generar`, { method: 'POST' }); toast('Generada ' + r.numero); closeModal(); location.href = `/api/documentos/${id}/pdf`; location.hash = 'documentos'; route(); });
  });
});
window.docEstado = guard(async (id, estado, msg) => {
  if (msg && !confirm(msg)) return; const r = await api(`/api/documentos/${id}/estado`, { method: 'POST', body: { estado } });
  toast(r.oportunidad || 'Estado actualizado');
  if (r.sugerencia) { if (confirm('Propuesta aceptada. ¿Marcar la oportunidad como GANADA ahora?')) moverOpp(r.sugerencia.oportunidad_id, 'Ganado'); }
  route();
});
async function vDocs() {
  const f = ST.docf || {}; const [ds, fe] = await Promise.all([api('/api/documentos?' + qs(f)), api('/api/facturacion/estado')]);
  const acc = d => { const e = d.estado; const a = [];
    if (d.version) a.push(`<a class="btn sm" href="/api/documentos/${d.id}/pdf">PDF</a>`);
    if (['borrador', 'generado'].includes(e)) a.push(d.tipo === 'propuesta' ? `<button class="btn sm" onclick="docPropuesta(${d.empresa_id},${d.id})">Editar</button>` : '');
    if (e === 'borrador') a.push(`<button class="btn sm primary" onclick="docGenerar(${d.id})">Generar</button>`);
    if (e === 'generado') a.push(`<button class="btn sm primary" onclick="docEstado(${d.id},'enviado','¿Confirma que YA envió este documento al cliente?')">Marcar enviado</button>`);
    if (d.tipo === 'propuesta' && e === 'enviado') a.push(`<button class="btn sm primary" onclick="docEstado(${d.id},'aceptado')">Aceptada</button>`);
    if (d.tipo === 'propuesta' && ['generado', 'enviado'].includes(e)) a.push(`<button class="btn sm" onclick="docEstado(${d.id},'rechazado')">Rechazada</button>`);
    if (['enviado', 'generado'].includes(e)) a.push(`<button class="btn sm" onclick="mensaje(${d.empresa_id},'${d.tipo === 'propuesta' ? 'envio_propuesta' : 'recordatorio_cobro'}','whatsapp',${d.id})">Mensaje</button>`);
    if (!['anulado', 'pagado'].includes(e)) a.push(`<button class="btn sm danger" onclick="docEstado(${d.id},'anulado','¿Anular ${d.numero || 'este borrador'}? El número no se reutiliza.')">Anular</button>`);
    return a.join(' '); };
  view.innerHTML = `<h1>Documentos</h1><div class="note">${esc(fe.explicacion)}</div>
  <div class="row" style="margin-bottom:10px"><select id="dt"><option value="">Todos los tipos</option><option value="propuesta">Propuestas</option><option value="cuenta_cobro">Cuentas de cobro</option></select>
  <select id="de"><option value="">Todos los estados</option>${['borrador', 'generado', 'enviado', 'aceptado', 'rechazado', 'pagado', 'anulado'].map(x => `<option>${x}</option>`).join('')}</select><span class="mut sm">«Vencido» se calcula por la fecha límite y no cambia el estado guardado.</span></div>
  <div class="tw keep"><table><thead><tr><th>Número</th><th>Cliente</th><th>Tipo</th><th class="r">Valor</th><th>Estado</th><th>Emisión</th><th>Límite</th><th class="r">Cobrado</th><th></th></tr></thead><tbody>
  ${ds.length ? ds.map(d => `<tr><td><b>${esc(d.numero || '(borrador)')}</b>${d.version ? `<div class="sm mut">v${d.version}</div>` : ''}</td><td><a href="#empresa/${d.empresa_id}">${esc(d.razon)}</a><div class="sm mut">${esc(d.titulo || '')}</div></td><td>${d.tipo === 'propuesta' ? 'Propuesta' : 'Cuenta de cobro'}</td>
  <td class="r">${COP(d.valor)}</td><td>${stTag(d.estado_efectivo)}${d.estado_efectivo !== d.estado ? `<div class="sm mut">(guardado: ${d.estado})</div>` : ''}</td><td class="sm">${d.fecha_emision || '—'}</td><td class="sm">${d.fecha_vigencia || '—'}</td><td class="r sm">${d.tipo === 'cuenta_cobro' ? COP(d.cobrado) : '—'}</td><td class="acts">${acc(d)}</td></tr>`).join('') : '<tr><td colspan="9" class="empty">Sin documentos. Cree una propuesta desde la ficha de la empresa o una cuenta de cobro desde una venta ganada.</td></tr>'}</tbody></table></div>`;
  $('#dt').value = f.tipo || ''; $('#de').value = f.estado || '';
  $('#dt').onchange = $('#de').onchange = () => { ST.docf = { tipo: $('#dt').value, estado: $('#de').value }; vDocs(); };
}
window.docGenerar = guard(async id => { const r = await api(`/api/documentos/${id}/generar`, { method: 'POST' }); toast('Generada ' + r.numero); location.href = `/api/documentos/${id}/pdf`; route(); });

/* ---------- calidad de datos ---------- */
async function vCalidad() {
  const est = ST.calE || 'excluido', pg = ST.calP || 1, mot = ST.calM || '';
  const [r, l] = await Promise.all([api('/api/calidad/resumen'), api(`/api/calidad/lista?estado=${est}&page=${pg}&motivo=${encodeURIComponent(mot)}`)]);
  const E = r.estados, kp = (t, k, c) => `<div class="card kpi" style="cursor:pointer" onclick="ST.calE='${k}';ST.calP=1;ST.calM='';vCalidad()"><div class="l">${t}</div><div class="v" style="color:${c}">${NUM(E[k] || 0)}</div></div>`;
  view.innerHTML = `<div class="row sp"><h1>Calidad de datos</h1><div class="acts"><a class="btn" href="/api/calidad/exportar.csv?estado=excluido">⤓ Excluidas (CSV)</a><a class="btn" href="/api/calidad/exportar.csv?estado=revisar">⤓ Por revisar (CSV)</a><button class="btn primary" id="bcl">Reclasificar</button></div></div>
  <div class="note">Nada se elimina: «excluida» solo saca la empresa de las listas de prospección. Toda decisión queda en el historial de cambios y puede revertirse. Reclasificar respeta sus decisiones manuales (${r.manuales}).</div>
  <div class="grid g4">${kp('Válidas', 'valido', 'var(--ok)')}${kp('Por revisar', 'revisar', 'var(--warn)')}${kp('Excluidas', 'excluido', 'var(--bad)')}</div>
  <div class="card" style="margin-top:12px"><h2>Motivos detectados</h2><div class="chips">${r.motivos.map(([m, n]) => `<button class="${mot === m ? 'on' : ''}" data-m="${esc(m)}">${esc(m)} · ${n}</button>`).join('')}</div></div>
  <div class="tw keep"><table><thead><tr><th>Empresa</th><th>NIT</th><th>Ciudad</th><th>Motivo</th><th></th></tr></thead><tbody>${l.items.length ? l.items.map(x => `<tr><td><a href="#empresa/${x.id}">${esc(x.razon)}</a>${x.calidad_manual ? ' <span class="tag">decisión manual</span>' : ''}</td><td class="sm">${esc(x.nit || '—')}</td><td class="sm">${esc(x.ciudad || '')}</td><td class="sm">${esc(x.calidad_motivo || '')}</td>
   <td class="acts">${x.calidad === 'valido' ? '' : `<button class="btn sm" onclick="calManual(${x.id},'valido')">${x.calidad === 'excluido' ? 'Restaurar' : 'Marcar válida'}</button>`}${x.calidad === 'excluido' ? '' : `<button class="btn sm danger" onclick="calManual(${x.id},'excluido')">Excluir</button>`}</td></tr>`).join('') : '<tr><td colspan="5" class="empty">Sin registros en esta vista.</td></tr>'}</tbody></table></div>
  <div class="pg"><button class="btn sm" ${pg <= 1 ? 'disabled' : ''} id="cp">←</button> ${pg} / ${Math.max(1, Math.ceil(l.total / l.per))} (${NUM(l.total)}) <button class="btn sm" ${pg >= Math.ceil(l.total / l.per) ? 'disabled' : ''} id="cn2">→</button></div>`;
  $('#bcl').onclick = guard(async () => { const x = await api('/api/calidad/clasificar', { method: 'POST' }); toast(`Evaluadas ${NUM(x.evaluadas)}: ${x.valido} válidas, ${x.revisar} por revisar, ${x.excluido} excluidas`); vCalidad(); });
  $$('.chips button[data-m]').forEach(b => b.onclick = () => { ST.calM = ST.calM === b.dataset.m ? '' : b.dataset.m; ST.calE = ST.calM && !E.excluido ? 'revisar' : ST.calE; ST.calP = 1; vCalidad(); });
  $('#cp').onclick = () => { ST.calP = pg - 1; vCalidad(); }; $('#cn2').onclick = () => { ST.calP = pg + 1; vCalidad(); };
}
window.calManual = guard(async (id, estado) => { let motivo = ''; if (estado === 'excluido') { motivo = prompt('Motivo de la exclusión:', 'No es cliente potencial') || ''; }
  await api(`/api/empresas/${id}/calidad`, { method: 'POST', body: { estado, motivo } }); toast(estado === 'excluido' ? 'Empresa excluida de la prospección' : 'Empresa restaurada'); route(); });

/* ---------- ficha de empresa: puntaje, documentos, tareas, acciones ---------- */
window.extraEmpresa = async e => {
  const [docs, tareas, aud] = await Promise.all([api('/api/documentos?empresa_id=' + e.id), api('/api/tareas?empresa_id=' + e.id), api('/api/auditoria?entidad=empresa&id=' + e.id)]);
  const det = e.score_detalle ? JSON.parse(e.score_detalle) : null; const ganadas = e.oportunidades.filter(o => o.etapa === 'Ganado');
  $('.row.sp', view).insertAdjacentHTML('afterend', `<div class="acts" style="margin:10px 0"><button class="btn" onclick="mensaje(${e.id},'primer_contacto','whatsapp')">💬 WhatsApp</button><button class="btn" onclick="mensaje(${e.id},'primer_contacto','correo')">✉ Correo</button>
   <button class="btn" onclick="docPropuesta(${e.id})">+ Propuesta</button><button class="btn" onclick="nuevaTarea(${e.id})">+ Tarea</button></div>
   ${e.calidad === 'excluido' ? `<div class="note">Empresa <b>excluida</b> de la prospección: ${esc(e.calidad_motivo || '')} <button class="btn sm" onclick="calManual(${e.id},'valido')">Restaurar</button></div>` : e.calidad === 'revisar' ? `<div class="note">Calidad por revisar: ${esc(e.calidad_motivo || '')} <button class="btn sm" onclick="calManual(${e.id},'valido')">Marcar válida</button> <button class="btn sm danger" onclick="calManual(${e.id},'excluido')">Excluir</button></div>` : ''}`);
  const col = $$('.grid.g2 > div', view)[1];
  col.insertAdjacentHTML('afterbegin', `<div class="card"><h2>Puntaje comercial <span class="tag P1">${e.score ?? '—'}</span></h2>${det && det.ubicacion ? Object.entries(det).map(([k, [p, mx, t]]) => `<div class="sc"><span>${k}</span><div class="bar"><i style="width:${mx ? p / mx * 100 : 0}%"></i></div><span class="mut">${p}/${mx}</span></div><div class="sm mut" style="margin:-2px 0 2px 116px">${esc(t)}</div>`).join('') : `<div class="sm mut">${esc((det && det.nota) || 'Sin cálculo.')}</div>`}
   <div class="sm mut" style="margin-top:6px">Es un orden de prioridad configurable, no una probabilidad de cierre.</div></div>
   <div class="card"><div class="row sp"><h2>Documentos</h2></div>${docs.length ? docs.map(d => `<div class="item"><div class="main"><b>${esc(d.numero || '(borrador)')}</b> ${stTag(d.estado_efectivo)} ${COP(d.valor)}<div class="sm mut">${esc(d.titulo || '')}</div></div>${d.version ? `<a class="btn sm" href="/api/documentos/${d.id}/pdf">PDF</a>` : ''}</div>`).join('') : '<div class="sm mut">Sin documentos.</div>'}
   ${ganadas.map(o => `<button class="btn sm acc" style="margin-top:8px" onclick="docCuenta(${o.id})">Cuenta de cobro (venta ${COP(o.valor_cerrado)})</button>`).join(' ')}<a class="sm" style="margin-left:8px" href="#documentos">Ver todos</a></div>
   <div class="card"><h2>Tareas pendientes <span class="tag">${tareas.length}</span></h2>${tareas.map(k => `<div class="item"><div class="main"><b>${esc(k.titulo)}</b><div class="sm mut">${fmtF(k.fecha)}${k.hora ? ' ' + k.hora : ''}</div></div><button class="btn sm" onclick="tareaHecha(${k.id})">Hecha</button></div>`).join('') || '<div class="sm mut">Ninguna.</div>'}</div>`);
  if (aud.length) col.insertAdjacentHTML('beforeend', `<details class="card"><summary class="b">Historial de cambios (${aud.length})</summary>${aud.map(a => `<div class="sm mut" style="margin-top:6px">${esc(a.fecha)} · <b>${esc(a.campo)}</b>: ${esc(a.antes ?? '∅')} → ${esc(a.despues ?? '∅')}${a.motivo ? ' · ' + esc(a.motivo) : ''}${a.usuario ? ' · ' + esc(a.usuario) : ''}</div>`).join('')}</details>`);
};

/* ---------- cobro con cuenta de cobro asociada ---------- */
window.cobro = guard(async oid => {
  const o = await api('/api/oportunidades/' + oid); const saldo = o.valor_cerrado - o.cobrado;
  const docs = (await api(`/api/documentos?oportunidad_id=${oid}&tipo=cuenta_cobro`)).filter(d => !['anulado', 'pagado'].includes(d.estado));
  modal('Registrar cobro · ' + o.razon, `<div class="sm">Vendido ${COP(o.valor_cerrado)} · Cobrado ${COP(o.cobrado)} · <b>Saldo ${COP(saldo)}</b></div>
   <form id="fc"><label>Monto cobrado (COP) *</label><input name="monto" inputmode="numeric" value="${saldo > 0 ? Math.round(saldo) : ''}"><label>Fecha</label><input type="date" name="fecha" value="${hoyStr()}">
   ${docs.length ? `<label>Aplicar a la cuenta de cobro</label><select name="documento_id"><option value="">— ninguna —</option>${docs.map(d => `<option value="${d.id}">${d.numero} · saldo ${COP(d.saldo)}</option>`).join('')}</select>` : ''}
   <label>Nota</label><input name="nota" style="width:100%" placeholder="Transferencia, referencia…"><div class="note" style="margin-top:8px">Registre solo dinero que ya recibió.</div>
   <div class="row" style="margin-top:14px"><button class="btn primary">Registrar cobro</button></div></form>
   ${o.cobros.length ? '<h2 style="margin-top:14px">Cobros previos</h2>' + o.cobros.map(c => `<div class="item"><div class="main">${COP(c.monto)} · ${fmtF(c.fecha)} <span class="mut sm">${esc(c.nota || '')}</span></div><button class="btn sm danger" onclick="delCobro(${c.id})">×</button></div>`).join('') : ''}`, m => {
    $('#fc', m).onsubmit = guard(async ev => { ev.preventDefault(); const b = formData(m); await api(`/api/oportunidades/${oid}/cobros`, { method: 'POST', body: b }); closeModal(); toast('Cobro registrado'); route(); });
  });
});

/* ---------- HOY: ámbito de prospección ---------- */
const _vHoy = window.vHoy;
window.vHoy = async () => {
  await _vHoy();
  const h = $$('.card h2', view).find(x => x.textContent.startsWith('Nuevas empresas')); if (!h) return;
  const c = await api('/api/config/prospeccion');
  h.insertAdjacentHTML('afterend', `<div class="row sm" style="margin-bottom:6px">Ámbito: <select id="amb">${[['cali', 'Cali'], ['valle', 'Valle del Cauca'], ['todo', 'Todo el país']].map(([k, l]) => `<option value="${k}" ${c.ambito === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
   <label style="margin:0"><input type="checkbox" id="sv" ${c.solo_validos ? 'checked' : ''} style="width:auto"> solo datos válidos</label></div>`);
  $('#amb').onchange = $('#sv').onchange = guard(async () => { await api('/api/config/prospeccion', { method: 'PUT', body: { ambito: $('#amb').value, solo_validos: $('#sv').checked } }); vHoy(); });
};

/* ---------- Dashboard: bloque ejecutivo ---------- */
const _vDash = window.vDash;
window.vDash = async () => {
  await _vDash(); const d = await api('/api/dashboard/ejecutivo'); const D = d.documentos, P = D.propuesta, C = d.cuentas_cobro;
  const k = (l, v, s) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="sm mut">${s || ''}</div></div>`;
  const tb = (t, rs) => `<div class="card"><h2>${t}</h2>${rs.length ? `<table><thead><tr><th></th><th class="r">Opp.</th><th class="r">Gan.</th><th class="r">Vendido</th><th class="r">Cobrado</th></tr></thead><tbody>${rs.map(x => `<tr><td class="sm">${esc(x.grupo)}${x.etiqueta && x.etiqueta !== x.grupo ? '<div class="mut">' + esc(x.etiqueta.slice(0, 40)) + '</div>' : ''}</td><td class="r">${x.opps}</td><td class="r">${x.ganadas}</td><td class="r">${COP(x.vendido)}</td><td class="r">${COP(x.cobrado)}</td></tr>`).join('')}</tbody></table>` : '<div class="sm mut">Aún sin oportunidades.</div>'}</div>`;
  view.insertAdjacentHTML('beforeend', `<h1 style="margin-top:22px">Tablero ejecutivo</h1>
  <div class="grid g4">${k('Actividades vencidas', `<span style="color:${d.actividades.vencido ? 'var(--bad)' : 'inherit'}">${d.actividades.vencido}</span>`, `hoy ${d.actividades.hoy} · próximos 7 días ${d.actividades.proximo}`)}
  ${k('Dinero recibido', COP(d.recibido.hoy), `semana ${COP(d.recibido.semana)} · mes ${COP(d.recibido.mes)}`)}
  ${k('Por cobrar (ventas ganadas)', COP(d.por_cobrar_ventas), `vendido ${COP(d.vendido_total)} · cobrado ${COP(d.cobrado_total)}`)}
  ${k('Cuentas de cobro pendientes', C.pendientes, `saldo ${COP(C.saldo)} · vencidas ${C.vencidas} (${COP(C.saldo_vencido)})`)}</div>
  <div class="grid g4" style="margin-top:12px">${k('Propuestas generadas', P.generado.n, COP(P.generado.v))}${k('Propuestas enviadas (sin respuesta)', P.enviado.n, COP(P.enviado.v))}${k('Propuestas aceptadas', P.aceptado.n, COP(P.aceptado.v) + ' · rechazadas ' + P.rechazado.n)}${k('Documentos emitidos este mes', d.emitidos_mes.n, COP(d.emitidos_mes.v))}</div>
  <div class="grid g2" style="margin-top:12px"><div class="card"><h2>Empresas prioritarias por contactar</h2>${d.prioritarias.map(x => `<div class="item"><div class="main"><a class="t" href="#empresa/${x.id}">${esc(x.razon)}</a><div class="sm mut">${esc(x.ciudad || '')} · puntaje ${x.score}</div></div><button class="btn sm" onclick="mensaje(${x.id})">Mensaje</button></div>`).join('') || '<div class="sm mut">Sin sugerencias en el ámbito elegido.</div>'}</div>
  <div class="card"><h2>Conversión entre etapas</h2><table><tbody>${d.conversion.map(x => `<tr><td class="sm">${x.etapa}</td><td class="r">${x.llegaron}</td><td class="r mut sm">${x.pct_vs_anterior == null ? '' : x.pct_vs_anterior + '% de la etapa previa'}</td></tr>`).join('')}</tbody></table><div class="sm mut">${esc(d.nota)}</div></div></div>
  <div class="grid g2">${tb('Sectores (CIIU) con mejores resultados', d.sectores)}${tb('Ubicaciones con mejores resultados', d.ubicaciones)}</div>`);
};

/* ---------- Configuración ---------- */
const fld = (n, l, v, ph = '') => `<div><label>${l}</label><input name="${n}" value="${esc(v ?? '')}" placeholder="${esc(ph)}"></div>`;
async function vConfig() {
  const [em, sc, pr, pls, es, rs, fe, au, ia] = await Promise.all([api('/api/config/emisor'), api('/api/config/scoring'), api('/api/config/prospeccion'), api('/api/plantillas'), api('/api/avisos/estado'), api('/api/respaldos'), api('/api/facturacion/estado'), api('/api/auth/estado'), api('/api/ia/estado')]);
  const c = sc.config;
  view.innerHTML = `<h1>Configuración</h1>
  <div class="card"><h2>Datos del emisor (aparecen en propuestas y cuentas de cobro)</h2><form id="fem"><div class="f2">${fld('nombre', 'Nombre completo *', em.nombre)}${fld('nombre_comercial', 'Nombre comercial', em.nombre_comercial)}${fld('documento', 'Documento de identificación', em.documento)}${fld('ciudad', 'Ciudad', em.ciudad)}${fld('direccion', 'Dirección', em.direccion)}${fld('telefono', 'Teléfono', em.telefono)}${fld('correo', 'Correo', em.correo)}${fld('titular_cuenta', 'Titular de la cuenta', em.titular_cuenta)}${fld('banco', 'Banco', em.banco)}${fld('tipo_cuenta', 'Tipo de cuenta', em.tipo_cuenta)}${fld('numero_cuenta', 'Número de cuenta', em.numero_cuenta)}${fld('vigencia_dias', 'Vigencia de propuestas (días)', em.vigencia_dias)}${fld('dias_pago', 'Plazo de pago (días)', em.dias_pago)}</div>
   ${campoTxt('nota_legal_cobro', 'Nota legal en la cuenta de cobro (la define usted/su contador)', em.nota_legal_cobro, 2)}${campoTxt('condiciones', 'Condiciones por defecto de propuestas', em.condiciones, 3)}<div class="row" style="margin-top:10px"><button class="btn primary">Guardar emisor</button></div></form></div>
  <div class="card"><h2>Prospección y puntaje</h2><div class="note">${esc(sc.explicacion)}<br><b>Los valores iniciales son supuestos editables, no hechos del mercado.</b></div>
   <form id="fsc"><div class="f2"><div><label>Ámbito de prospección</label><select name="ambito">${[['cali', 'Cali'], ['valle', 'Valle del Cauca'], ['todo', 'Todo el país']].map(([k, l]) => `<option value="${k}" ${pr.ambito === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
   <div><label>Casos de éxito (códigos CIIU, separados por coma)</label><input name="casos" value="${esc(c.casos_exito_ciiu.join(', '))}" placeholder="Ej.: 5611, 1410"></div></div>
   <label>Pesos (0-100)</label><div class="row">${Object.entries(c.pesos).map(([k, v]) => `<span class="sm">${k} <input style="width:60px" name="p_${k}" value="${v}"></span>`).join('')}</div>
   <div class="f2"><div><label>Tamaño objetivo: percentil mínimo</label><input name="tmin" value="${c.tamano_pct.min}"></div><div><label>percentil máximo</label><input name="tmax" value="${c.tamano_pct.max}"></div></div>
   ${campoTxt('ubic', 'Ubicación (JSON: ciudades, deptos, otros)', JSON.stringify(c.ubicacion, null, 1), 6)}${campoTxt('sect', 'Sectores por división CIIU (JSON: "46": 0.8)', JSON.stringify(c.sectores), 4)}
   <div class="row" style="margin-top:10px"><button class="btn primary">Guardar y recalcular puntajes</button></div></form></div>
  <div class="card"><h2>Plantillas de mensajes</h2><div class="sm mut">Variables: {saludo} {empresa} {sector} {ciudad} {mi_nombre} {mi_empresa} {propuesta} {vigencia} {cuenta_cobro} {saldo}</div>
   ${pls.map(p => `<details style="margin-top:8px"><summary><b>${esc(p.nombre)}</b> · ${p.canal}</summary><form class="fpl" data-id="${p.id}">${p.canal === 'correo' ? `<label>Asunto</label><input name="asunto" style="width:100%" value="${esc(p.asunto)}">` : ''}${campoTxt('cuerpo', 'Mensaje', p.cuerpo, 5)}<button class="btn sm primary" style="margin-top:6px">Guardar</button></form></details>`).join('')}</div>
  <div class="card"><h2>Avisos</h2><div class="sm">${esc(es.nota)}</div><div class="sm" style="margin:8px 0">Resumen por correo: <b>${es.correo_configurado ? 'configurado → ' + esc(es.destino) : 'no configurado'}</b>${es.ultimo_resumen ? ' · último envío ' + es.ultimo_resumen : ''}. Se configura con variables de entorno (ver README); las claves nunca se guardan en el código ni en la base.</div>
   <div class="acts"><button class="btn" id="bnot">Activar avisos de escritorio (navegador)</button><button class="btn" id="bmail" ${es.correo_configurado ? '' : 'disabled'}>Enviar resumen de prueba</button></div></div>
  <div class="card"><h2>Asistencia IA</h2><div class="sm">OpenAI: ${ia.api_key_configurada ? 'variable configurada' : 'sin configurar'} · integración ${esc(ia.integracion)} · modelo ${esc(ia.modelo_configurado)}. Esta versión no realiza solicitudes externas. Gemini aún no está integrado.</div></div>
  <div class="card"><h2>Seguridad y copias</h2><div class="sm">Sesión: <b>${esc(au.usuario || '—')}</b> · <a href="#" id="lo">Cerrar sesión</a></div>
   <form id="fcl" class="f2"><div><label>Contraseña actual</label><input type="password" name="actual"></div><div><label>Nueva (mín. 10)</label><input type="password" name="nueva"></div><div><button class="btn sm primary" style="margin-top:8px">Cambiar contraseña</button></div></form>
   <div class="row sp" style="margin-top:12px"><h2 style="margin:0">Copias de seguridad (${rs.length})</h2><button class="btn" id="bres">Crear copia ahora</button></div>
   ${rs.slice(0, 8).map(r => `<div class="sm"><a href="/api/respaldos/${r.archivo}">${r.archivo}</a> · ${r.fecha} · ${(r.bytes / 1e6).toFixed(1)} MB</div>`).join('') || '<div class="sm mut">Aún sin copias.</div>'}
   <div class="note" style="margin-top:10px">Restaurar (con el CRM cerrado): <code>python restaurar.py data\\respaldos\\ARCHIVO.db</code>. Verifica la copia y guarda antes la base actual.</div></div>
  <div class="card"><h2>Facturación electrónica</h2><div class="sm">${esc(fe.explicacion)}</div></div>`;
  $('#fem').onsubmit = guard(async ev => { ev.preventDefault(); await api('/api/config/emisor', { method: 'PUT', body: formData($('#fem')) }); toast('Emisor guardado'); });
  $('#fsc').onsubmit = guard(async ev => { ev.preventDefault(); const f = formData($('#fsc')); const pesos = {}; Object.keys(c.pesos).forEach(k => pesos[k] = num(f['p_' + k]));
    let ubicacion, sectores; try { ubicacion = JSON.parse(f.ubic); sectores = JSON.parse(f.sect); } catch (e) { throw new Error('El JSON de ubicación o sectores no es válido.'); }
    await api('/api/config/prospeccion', { method: 'PUT', body: { ambito: f.ambito } });
    const r = await api('/api/config/scoring', { method: 'PUT', body: { pesos, ubicacion, sectores, casos_exito_ciiu: f.casos.split(',').map(x => x.trim()).filter(Boolean), tamano_pct: { min: num(f.tmin), max: num(f.tmax) } } }); toast(`Puntajes recalculados (${NUM(r.recalculadas)})`); });
  $$('.fpl').forEach(f => f.onsubmit = guard(async ev => { ev.preventDefault(); await api('/api/plantillas/' + f.dataset.id, { method: 'PUT', body: formData(f) }); toast('Plantilla guardada'); }));
  $('#bnot').onclick = async () => { const p = await Notification.requestPermission(); toast(p === 'granted' ? 'Avisos activados (solo con el CRM abierto)' : 'Permiso no concedido', p !== 'granted'); };
  $('#bmail').onclick = guard(async () => { await api('/api/avisos/probar-correo', { method: 'POST' }); toast('Resumen enviado'); });
  $('#fcl').onsubmit = guard(async ev => { ev.preventDefault(); await api('/api/auth/cambiar', { method: 'POST', body: formData($('#fcl')) }); toast('Contraseña cambiada'); ev.target.reset(); });
  $('#lo').onclick = async ev => { ev.preventDefault(); await api('/api/auth/logout', { method: 'POST' }); location.href = '/login'; };
  $('#bres').onclick = guard(async () => { const r = await api('/api/respaldos', { method: 'POST' }); toast('Copia verificada: ' + r.archivo); vConfig(); });
}
