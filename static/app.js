/* VELTRIX CRM - interfaz (JS vanilla, sin dependencias) */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const COP = n => '$' + Math.round(n || 0).toLocaleString('es-CO');
const NUM = n => n == null ? '—' : Math.round(n).toLocaleString('es-CO');
const view = $('#view');
let CFG = null;
const ST = { emp: { page: 1, f: {}, orden: 'razon' } };

async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: {} };
  if (opts.body instanceof FormData) o.body = opts.body;
  else if (opts.body) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o);
  if (r.status === 401) { location.href = '/login'; throw new Error('Sesión requerida'); }
  let j = null; try { j = await r.json(); } catch (e) { }
  if (!r.ok) { const err = new Error((j && j.error) || 'Error de servidor'); err.data = j; throw err; }
  return j;
}
function toast(msg, err) {
  const d = document.createElement('div'); d.className = 't1' + (err ? ' err' : ''); d.textContent = msg;
  $('#toast').append(d); setTimeout(() => d.remove(), err ? 5000 : 2500);
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };

/* ---------- utilidades de fecha ---------- */
const iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
function hoyStr() { return CFG.hoy; }
function addDias(n) { const d = new Date(hoyStr() + 'T12:00:00'); d.setDate(d.getDate() + n); return iso(d); }
function fmtF(f) {
  if (!f) return '—'; if (f === hoyStr()) return 'Hoy'; if (f === addDias(1)) return 'Mañana'; if (f === addDias(-1)) return 'Ayer';
  const d = new Date(f + 'T12:00:00'); return d.toLocaleDateString('es-CO', { day: 'numeric', month: 'short' });
}

/* ---------- modal ---------- */
function modal(title, html, onReady) {
  $('#mt').textContent = title; $('#mb').innerHTML = html; $('#modal').classList.remove('hidden');
  if (onReady) onReady($('#mb')); const f = $('#mb input:not([type=hidden]),#mb select,#mb textarea'); if (f) setTimeout(() => f.focus(), 30);
}
const closeModal = () => $('#modal').classList.add('hidden');
$('#mx').onclick = closeModal;
$('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
  if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); $('#busq').focus(); }
});
function formData(root) { const o = {}; $$('[name]', root).forEach(i => { o[i.name] = i.type === 'checkbox' ? i.checked : i.value; }); return o; }
function fechaChips(name = 'proxima_fecha') {
  return `<div class="chips" data-for="${name}">${[['Hoy', 0], ['Mañana', 1], ['+3 días', 3], ['+1 semana', 7], ['+2 semanas', 14]].map(([l, n]) => `<button type="button" data-d="${addDias(n)}">${l}</button>`).join('')}</div>`;
}
function wireChips(root) {
  $$('.chips[data-for]', root).forEach(c => c.onclick = e => {
    const b = e.target.closest('button'); if (!b) return; $(`[name=${c.dataset.for}]`, root).value = b.dataset.d;
    $$('button', c).forEach(x => x.classList.toggle('on', x === b));
  });
}

/* ---------- componentes ---------- */
const tagP = p => `<span class="tag ${p}">${p}</span>`;
function badgeOpp(o) {
  let h = `<span class="tag">${esc(o.etapa)}</span>`;
  if (o.vencido) h += ' <span class="tag red">Vencido</span>';
  if (o.sin_proximo_paso) h += ' <span class="tag amb">Sin próximo paso</span>';
  if (o.es_caliente) h += ' <span class="tag P1">★ Caliente</span>';
  return h;
}
function oppItem(o, extra = '') {
  return `<div class="item"><div class="main">
    <a class="t" href="#empresa/${o.empresa_id}">${esc(o.razon)}</a> ${tagP(o.prioridad)} ${badgeOpp(o)}
    <div class="sm mut">${o.proximo_paso ? '➜ ' + esc(o.proximo_paso) + ' · ' + fmtF(o.proxima_fecha) : 'Defina el próximo paso'}${o.contacto ? ' · ' + esc(o.contacto) : ''}${o.telefono ? ' · ' + esc(o.telefono) : ''}${o.valor_estimado ? ' · ' + COP(o.valor_estimado) : ''}</div></div>
    <div class="acts">${extra}<button class="btn sm primary" onclick="regContacto(${o.empresa_id},${o.id})">Registrar contacto</button><button class="btn sm" onclick="mensaje(${o.empresa_id},'seguimiento')">Mensaje</button><button class="btn sm" onclick="editarPaso(${o.id})">Reprogramar</button></div></div>`;
}

/* ---------- router ---------- */
async function route() {
  if (!CFG) CFG = await api('/api/config');
  const [r, arg] = (location.hash.slice(1) || 'hoy').split('/');
  $$('#side a[data-r]').forEach(a => a.classList.toggle('on', a.dataset.r === r || (r === 'empresa' && a.dataset.r === 'empresas')));
  try {
    await ({ hoy: vHoy, pipeline: vPipeline, empresas: vEmpresas, empresa: () => vEmpresa(arg), seguimientos: vSeg, dashboard: vDash, importar: vImportar, ...(window.EXTRA_ROUTES || {}) }[r] || vHoy)();
  } catch (e) { view.innerHTML = `<div class="card">Error: ${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', route);

/* ============================ HOY ============================ */
async function vHoy() {
  const d = await api('/api/hoy'); const h = d.resumen, m = (await api('/api/metas'));
  const prog = (a, b) => `<div class="bar"><i class="${a >= b ? 'full' : ''}" style="width:${Math.min(100, b ? a / b * 100 : 0)}%"></i></div>`;
  const kp = (l, a, b) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${a}<span class="mut sm"> / ${b}</span></div>${prog(a, b)}</div>`;
  const sec = (t, arr, fn, vacio) => `<div class="card"><h2>${t} <span class="tag">${arr.length}</span></h2>${arr.length ? arr.slice(0, 25).map(fn).join('') + (arr.length > 25 ? `<div class="sm mut">…y ${arr.length - 25} más (ver Seguimientos)</div>` : '') : `<div class="sm mut">${vacio}</div>`}</div>`;
  view.innerHTML = `<h1>Hoy · ${new Date(d.fecha + 'T12:00:00').toLocaleDateString('es-CO', { weekday: 'long', day: 'numeric', month: 'long' })}</h1>
  <div class="grid g4">${kp('Contactos nuevos', h.contactos_nuevos, m.contactos_nuevos)}${kp('Seguimientos', h.seguimientos, m.seguimientos)}${kp('Propuestas', h.propuestas, m.propuestas)}${kp('Reuniones agendadas', h.reuniones, m.reuniones)}
  <div class="card kpi"><div class="l">Cobrado hoy (referencia ${COP(m.cobro_diario)})</div><div class="v">${COP(h.cobrado)}</div>${prog(h.cobrado, m.cobro_diario)}</div></div>
  ${d.sin_paso.length ? `<div class="note">⚠ ${d.sin_paso.length} oportunidad(es) activa(s) sin próximo paso: <a href="#seguimientos">corregir ahora</a></div>` : ''}
  <div class="grid g2"><div>
  ${sec('★ Oportunidades calientes', d.calientes, o => oppItem(o), 'No hay oportunidades calientes. Califique una oportunidad para verla aquí.')}
  ${sec('Seguimientos vencidos', d.vencidos, o => oppItem(o), 'Sin seguimientos vencidos. 👌')}
  ${sec('Programados para hoy', d.programados, o => oppItem(o), 'Nada más programado para hoy.')}
  </div><div>
  ${sec('Pipeline: por contactar', d.por_contactar, o => oppItem(o), 'Ninguna en el pipeline por contactar.')}
  <div class="card"><h2>Nuevas empresas para contactar <span class="tag">${d.nuevas.length}</span></h2>
  <div class="sm mut" style="margin-bottom:6px">Ordenadas por puntaje comercial. Faltan ${Math.max(m.contactos_nuevos - h.contactos_nuevos, 0)} para la meta de hoy.</div>
  ${d.nuevas.length ? d.nuevas.map(e => `<div class="item"><div class="main"><a class="t" href="#empresa/${e.id}">${esc(e.razon)}</a> ${tagP(e.prioridad)}<div class="sm mut">${esc(e.ciudad || '')} · ${esc(e.sector || '')}${e.n_contactos ? '' : ' · sin contacto registrado'} · puntaje ${e.score ?? '—'}</div></div>
  <div class="acts"><button class="btn sm primary" onclick="regContacto(${e.id})">Registrar contacto</button><button class="btn sm" onclick="mensaje(${e.id})">Mensaje</button><button class="btn sm" onclick="agregarPipeline(${e.id})">+ Pipeline</button></div></div>`).join('') : '<div class="sm mut">No quedan empresas sin trabajar.</div>'}</div>
  </div></div>`;
}

/* ============================ EMPRESAS ============================ */
async function vEmpresas() {
  const fac = await api('/api/empresas/facetas'); const s = ST.emp, f = s.f;
  view.innerHTML = `<div class="row sp"><h1>Empresas <span class="mut sm" id="cnt"></span></h1><div class="acts"><button class="btn" id="bExp">⤓ Exportar CSV</button><a class="btn" href="#importar">⇪ Importar</a></div></div>
  <div class="card"><div class="row">
   <select id="fP"><option value="">Prioridad</option><option>P1</option><option>P2</option><option>P3</option></select>
   <select id="fD"><option value="">Departamento</option>${fac.deptos.map(x => `<option>${esc(x)}</option>`).join('')}</select>
   <select id="fC"><option value="">Ciudad</option></select>
   <select id="fT"><option value="">Tipo</option>${fac.tipos.map(x => `<option>${esc(x)}</option>`).join('')}</select>
   <input id="fS" placeholder="Sector / actividad" style="width:170px">
   <select id="fPi"><option value="">Pipeline: todas</option><option value="sin">Sin oportunidad</option><option value="con">Con oportunidad activa</option></select>
   <select id="fCo"><option value="">Contactos: todas</option><option value="con">Con contacto</option><option value="sin">Sin contacto</option></select>
   <select id="fX"><option value="">Calidad: todas</option><option value="dup">Posibles duplicados</option><option value="rev">Por revisar</option><option value="val">Solo válidas</option><option value="excl">Excluidas</option></select>
   <select id="fO"><option value="razon">Orden: nombre</option><option value="score">Mayor puntaje</option><option value="activos">Mayor activos</option><option value="prioridad">Prioridad</option><option value="reciente">Más recientes</option></select>
   <button class="btn ghost" id="fR">Limpiar</button></div></div>
  <div class="tw"><table><thead><tr><th>Empresa</th><th>Prior.</th><th>Ciudad</th><th>Sector</th><th class="r">Activos</th><th class="r">Puntaje</th><th>Contacto</th><th>Pipeline</th><th></th></tr></thead><tbody id="tb"></tbody></table></div><div class="pg" id="pg"></div>`;
  const set = (id, v) => { if (v) $(id).value = v; };
  set('#fP', f.prioridad); set('#fD', f.depto); set('#fT', f.tipo); set('#fS', f.sector); set('#fPi', f.pipeline); set('#fCo', f.contacto);
  set('#fX', f.dup ? 'dup' : f.revisar ? 'rev' : f.calidad === 'valido' ? 'val' : f.calidad === 'excluido' ? 'excl' : ''); set('#fO', s.orden);
  const loadCities = async () => { const c = await api('/api/empresas/ciudades' + (f.depto ? '?depto=' + encodeURIComponent(f.depto) : '')); $('#fC').innerHTML = '<option value="">Ciudad</option>' + c.map(x => `<option>${esc(x)}</option>`).join(''); set('#fC', f.ciudad); };
  await loadCities();
  const read = () => {
    const x = $('#fX').value; s.f = { prioridad: $('#fP').value, depto: $('#fD').value, ciudad: $('#fC').value, tipo: $('#fT').value, sector: $('#fS').value, pipeline: $('#fPi').value, contacto: $('#fCo').value, dup: x === 'dup' ? '1' : '', revisar: x === 'rev' ? '1' : '', calidad: x === 'val' ? 'valido' : x === 'excl' ? 'excluido' : '' };
    if ($('#busq').value.trim()) s.f.q = $('#busq').value.trim(); s.orden = $('#fO').value; return s.f;
  };
  const reload = guard(async (resetPage) => { if (resetPage) s.page = 1; read(); await cargarEmpresas(); });
  $$('#fP,#fT,#fPi,#fCo,#fX,#fO,#fC').forEach(e => e.onchange = () => reload(true));
  $('#fD').onchange = async () => { f.depto = $('#fD').value; ST.emp.f.depto = f.depto; $('#fC').value = ''; ST.emp.f.ciudad = ''; await loadCities(); reload(true); };
  $('#fS').oninput = debounce(() => reload(true), 300);
  $('#fR').onclick = () => { s.f = {}; s.page = 1; $('#busq').value = ''; vEmpresas(); };
  $('#bExp').onclick = () => { read(); location.href = '/api/empresas/exportar?' + qs(s.f); };
  window.__reloadEmp = () => reload(true);
  if (f.q) $('#busq').value = f.q;
  await cargarEmpresas();
}
const qs = o => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v))).toString();
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
async function cargarEmpresas() {
  const s = ST.emp; const d = await api('/api/empresas?' + qs({ ...s.f, page: s.page, per: 50, orden: s.orden }));
  $('#cnt').textContent = `· ${d.total.toLocaleString('es-CO')} resultados`;
  $('#tb').innerHTML = d.items.length ? d.items.map(e => `<tr class="click" onclick="location.hash='empresa/${e.id}'">
   <td><b>${esc(e.razon)}</b><div class="sm mut">${esc(e.nit || 'sin NIT')}${(e.n_similares || e.filas_repetidas) && !e.dup_revisado ? ' <span class="tag amb">posible duplicado</span>' : ''}${e.revisar && !e.dup_revisado ? ' <span class="tag amb" title="' + esc(e.revisar) + '">revisar</span>' : ''}${e.calidad === 'excluido' ? ' <span class="tag red" title="' + esc(e.calidad_motivo || '') + '">excluida</span>' : ''}</div></td>
   <td>${tagP(e.prioridad)}</td><td>${esc(e.ciudad || '')}</td><td class="sm">${esc((e.sector || '').slice(0, 60))}</td><td class="r">${NUM(e.activos)}</td><td class="r sm">${e.score ?? '—'}</td>
   <td class="sm">${e.n_contactos || '<span class="mut">—</span>'}</td><td>${e.etapa_activa ? `<span class="tag">${esc(e.etapa_activa)}</span>` : '<span class="mut sm">—</span>'}</td>
   <td onclick="event.stopPropagation()" class="acts">${e.opp_activa ? '' : `<button class="btn sm" onclick="agregarPipeline(${e.id})">+ Pipeline</button>`}</td></tr>`).join('') : '<tr><td colspan="9" class="empty">Sin resultados.</td></tr>';
  const pages = Math.max(1, Math.ceil(d.total / d.per));
  $('#pg').innerHTML = `<button class="btn sm" ${s.page <= 1 ? 'disabled' : ''} id="pp">←</button> Página ${s.page} de ${pages} <button class="btn sm" ${s.page >= pages ? 'disabled' : ''} id="pn">→</button>`;
  $('#pp').onclick = () => { s.page--; cargarEmpresas(); }; $('#pn').onclick = () => { s.page++; cargarEmpresas(); };
}
$('#busq').addEventListener('input', debounce(() => {
  if (location.hash.startsWith('#empresas')) { ST.emp.f.q = $('#busq').value.trim(); ST.emp.page = 1; cargarEmpresas().catch(e => toast(e.message, true)); }
}, 250));
$('#busq').addEventListener('keydown', e => {
  if (e.key === 'Enter') { ST.emp.f = { q: $('#busq').value.trim() }; ST.emp.page = 1; if (location.hash.startsWith('#empresas')) vEmpresas(); else location.hash = 'empresas'; }
});

/* ---------- ficha de empresa ---------- */
async function vEmpresa(id) {
  const e = await api('/api/empresas/' + id);
  const opA = e.oportunidades.find(o => !['Ganado', 'Perdido'].includes(o.etapa));
  view.innerHTML = `<div class="row sp"><div><a href="#empresas" class="sm">← Empresas</a><h1 style="margin:4px 0">${esc(e.razon)} ${tagP(e.prioridad)}</h1><div class="mut sm">NIT ${esc(e.nit || '—')} · ${esc(e.ciudad || '')} ${esc(e.depto || '')} · ${esc(e.tipo || '')}</div></div>
  <div class="acts"><button class="btn primary" onclick="regContacto(${e.id},${opA ? opA.id : 'null'})">Registrar contacto</button>${opA ? '' : `<button class="btn acc" onclick="agregarPipeline(${e.id})">+ Pipeline</button>`}</div></div>
  ${(e.similares.length || e.filas_repetidas) && !e.dup_revisado ? `<div class="note">⚠ <b>Posible duplicado.</b> ${e.similares.length ? 'Razón social similar: ' + e.similares.map(x => `<a href="#empresa/${x.id}">${esc(x.razon)} (${esc(x.nit || 'sin NIT')})</a>`).join(', ') + '. ' : ''}${e.filas_repetidas ? `El NIT aparece ${e.filas_repetidas + 1} veces en el archivo importado (posibles periodos distintos); se conservó la primera fila. ` : ''}<button class="btn sm" onclick="marcarRevisado(${e.id})">Marcar como revisado</button></div>` : ''}
  ${e.revisar && !e.dup_revisado ? `<div class="note">Por revisar: ${esc(e.revisar)}</div>` : ''}
  <div class="grid g2"><div>
   <div class="card"><h2>Datos</h2><form id="fe"><div class="f2">
    <div><label>Razón social *</label><input name="razon" value="${esc(e.razon)}"></div><div><label>NIT</label><input name="nit" value="${esc(e.nit || '')}"></div>
    <div><label>Prioridad</label><select name="prioridad">${['P1', 'P2', 'P3'].map(p => `<option ${p === e.prioridad ? 'selected' : ''}>${p}</option>`).join('')}</select>${e.prioridad_auto ? '<div class="sm mut">Sugerida por tamaño (activos); cámbiela si lo requiere.</div>' : ''}</div>
    <div><label>Tipo</label><input name="tipo" value="${esc(e.tipo || '')}"></div>
    <div><label>Departamento</label><input name="depto" value="${esc(e.depto || '')}"></div><div><label>Ciudad</label><input name="ciudad" value="${esc(e.ciudad || '')}"></div>
    <div><label>Código CIIU</label><input name="ciiu" value="${esc(e.ciiu || '')}"></div><div><label>Sector</label><input name="sector" value="${esc(e.sector || '')}"></div>
    <div><label>Sitio web</label><input name="web" value="${esc(e.web || '')}"></div></div>
    <label>Notas</label><textarea name="notas">${esc(e.notas || '')}</textarea>
    <div class="row" style="margin-top:10px"><button class="btn primary" type="submit">Guardar</button><button class="btn danger sm" type="button" id="bDel">Borrar empresa</button></div></form>
    ${e.activos != null ? `<div class="sm mut" style="margin-top:10px">Datos financieros del archivo (sin convertir): Activos ${NUM(e.activos)} · Pasivos ${NUM(e.pasivos)} · Patrimonio ${NUM(e.patrimonio)} · Utilidad operacional ${NUM(e.utilidad_op)}</div>` : ''}</div>
   <div class="card"><div class="row sp"><h2>Contactos</h2><button class="btn sm" onclick="editarContacto(${e.id})">+ Contacto</button></div>
    ${e.contactos.length ? e.contactos.map(c => `<div class="item"><div class="main"><b>${esc(c.nombre || '(sin nombre)')}</b> <span class="mut sm">${esc(c.cargo || '')}</span><div class="sm">${c.telefono ? `<a href="tel:${esc(c.telefono)}">${esc(c.telefono)}</a>` : ''} ${c.correo ? `· <a href="mailto:${esc(c.correo)}">${esc(c.correo)}</a>` : ''}</div></div>
    <button class="btn sm" onclick='editarContacto(${e.id},${JSON.stringify(c).replace(/'/g, '&#39;')})'>Editar</button></div>`).join('') : '<div class="sm mut">Sin contactos todavía. La base original no trae teléfonos ni correos: regístrelos aquí.</div>'}</div>
  </div><div>
   <div class="card"><h2>Oportunidades</h2>${e.oportunidades.length ? e.oportunidades.map(o => `<div class="item"><div class="main"><span class="tag">${esc(o.etapa)}</span> <b>${COP(o.etapa === 'Ganado' ? o.valor_cerrado : o.valor_estimado)}</b>
     <div class="sm mut">${o.etapa === 'Ganado' ? `Cobrado ${COP(o.cobrado)} · Saldo ${COP(o.valor_cerrado - o.cobrado)}` : o.proximo_paso ? '➜ ' + esc(o.proximo_paso) + ' · ' + fmtF(o.proxima_fecha) : ''}${o.etapa === 'Perdido' && o.motivo_perdida ? 'Motivo: ' + esc(o.motivo_perdida) : ''}</div></div>
     <a class="btn sm" href="#pipeline">Ver en pipeline</a></div>`).join('') : '<div class="sm mut">Aún sin oportunidad.</div>'}</div>
   <div class="card"><h2>Historial de contactos</h2>${e.interacciones.length ? e.interacciones.map(i => `<div class="item"><div class="main"><b>${esc(i.tipo)}</b> · ${esc(i.resultado)} <span class="mut sm">${fmtF(i.fecha)}${i.contacto ? ' · ' + esc(i.contacto) : ''}</span>${i.notas ? `<div class="sm">${esc(i.notas)}</div>` : ''}</div></div>`).join('') : '<div class="sm mut">Sin contactos registrados.</div>'}</div>
   ${e.filas_origen.length ? `<details class="card"><summary class="b">Filas originales del archivo (${e.filas_origen.length})</summary>${e.filas_origen.map(f => `<div class="sm mut" style="margin-top:6px">Fila ${f.fila} · ${esc(f.estado)}${f.motivo ? ' · ' + esc(f.motivo) : ''}<br><code style="word-break:break-all">${esc((f.datos || '').slice(0, 400))}</code></div>`).join('')}</details>` : ''}
  </div></div>`;
  $('#fe').onsubmit = guard(async ev => { ev.preventDefault(); await api('/api/empresas/' + id, { method: 'PUT', body: formData($('#fe')) }); toast('Empresa guardada'); vEmpresa(id); });
  $('#bDel').onclick = guard(async () => { if (confirm('¿Borrar esta empresa y sus contactos?')) { await api('/api/empresas/' + id, { method: 'DELETE' }); location.hash = 'empresas'; } });
  if (window.extraEmpresa) await window.extraEmpresa(e);
}
window.marcarRevisado = guard(async id => { await api('/api/empresas/' + id, { method: 'PUT', body: { dup_revisado: 1 } }); toast('Marcada como revisada'); route(); });
window.editarContacto = (eid, c) => {
  c = c || {};
  modal(c.id ? 'Editar contacto' : 'Nuevo contacto', `<form id="fc"><div class="f2"><div><label>Nombre</label><input name="nombre" value="${esc(c.nombre || '')}"></div><div><label>Cargo</label><input name="cargo" value="${esc(c.cargo || '')}"></div>
  <div><label>Teléfono</label><input name="telefono" value="${esc(c.telefono || '')}"></div><div><label>Correo</label><input name="correo" type="email" value="${esc(c.correo || '')}"></div></div>
  <div class="row" style="margin-top:14px"><button class="btn primary">Guardar</button>${c.id ? '<button type="button" class="btn danger" id="cd">Eliminar</button>' : ''}</div></form>`, m => {
    $('#fc', m).onsubmit = guard(async ev => { ev.preventDefault(); const b = formData(m); if (c.id) await api('/api/contactos/' + c.id, { method: 'PUT', body: b }); else await api(`/api/empresas/${eid}/contactos`, { method: 'POST', body: b }); closeModal(); toast('Contacto guardado'); route(); });
    if (c.id) $('#cd', m).onclick = guard(async () => { await api('/api/contactos/' + c.id, { method: 'DELETE' }); closeModal(); route(); });
  });
};
$('#btnNueva').onclick = () => modal('Nueva empresa', `<form id="fn"><label>Razón social *</label><input name="razon" required><div class="f2"><div><label>NIT</label><input name="nit"></div><div><label>Prioridad</label><select name="prioridad"><option>P3</option><option>P2</option><option>P1</option></select></div>
 <div><label>Ciudad</label><input name="ciudad"></div><div><label>Sector</label><input name="sector"></div></div>
 <label>Contacto (opcional)</label><div class="f2"><input name="c_nombre" placeholder="Nombre"><input name="c_telefono" placeholder="Teléfono"></div><input name="c_correo" placeholder="Correo" style="width:100%;margin-top:6px">
 <div class="row" style="margin-top:14px"><button class="btn primary">Crear</button></div></form>`, m => {
  $('#fn', m).onsubmit = guard(async ev => { ev.preventDefault(); const b = formData(m); b.contacto = { nombre: b.c_nombre, telefono: b.c_telefono, correo: b.c_correo };
    try { const r = await api('/api/empresas', { method: 'POST', body: b }); closeModal(); location.hash = 'empresa/' + r.id; toast('Empresa creada'); }
    catch (er) { if (er.data && er.data.existente) { closeModal(); location.hash = 'empresa/' + er.data.existente; } throw er; } });
});

/* ============================ registrar contacto / seguimiento ============================ */
window.regContacto = guard(async (eid, oid) => {
  const e = await api('/api/empresas/' + eid);
  const opp = e.oportunidades.find(o => o.id === oid) || e.oportunidades.find(o => !['Ganado', 'Perdido'].includes(o.etapa));
  const res = ['Sin respuesta', 'Buzón / no contestó', 'Contestó: interesado', 'Contestó: pidió información', 'Contestó: no es el momento', 'Contestó: no interesado', 'Reunión acordada', 'Propuesta enviada', 'Otro'];
  modal('Registrar contacto · ' + e.razon, `<form id="fi"><input type="hidden" name="empresa_id" value="${eid}">${opp ? `<input type="hidden" name="oportunidad_id" value="${opp.id}">` : ''}
   <label>Tipo</label><div class="chips" id="tp">${CFG.tipos.map((t, i) => `<button type="button" data-v="${t}" class="${i === 0 ? 'on' : ''}">${t}</button>`).join('')}</div><input type="hidden" name="tipo" value="${CFG.tipos[0]}">
   <div class="f2"><div><label>Contacto</label><select name="contacto_id"><option value="">—</option>${e.contactos.map(c => `<option value="${c.id}">${esc(c.nombre || c.telefono || c.correo)}</option>`).join('')}</select></div>
   <div><label>Resultado *</label><select name="resultado">${res.map(r => `<option>${r}</option>`).join('')}</select></div></div>
   <label>Notas</label><textarea name="notas" placeholder="Qué se habló, objeciones, datos útiles"></textarea>
   <hr style="border:0;border-top:1px solid var(--line);margin:12px 0 0">
   <label>Próxima acción * ${opp ? '' : '(se creará la oportunidad en “Contactado”)'}</label><input name="proxima_accion" style="width:100%" placeholder="Ej.: Llamar para confirmar reunión" value="${opp && opp.proximo_paso && opp.proxima_fecha >= hoyStr() ? '' : ''}">
   <label>Fecha *</label><input type="date" name="proxima_fecha" value="${addDias(3)}">${fechaChips()}
   <div class="row" style="margin-top:14px"><button class="btn primary">Guardar contacto</button></div></form>`, m => {
    wireChips(m);
    $('#tp', m).onclick = ev => { const b = ev.target.closest('button'); if (!b) return; $$('button', $('#tp', m)).forEach(x => x.classList.toggle('on', x === b)); $('[name=tipo]', m).value = b.dataset.v; };
    $('#fi', m).onsubmit = guard(async ev => { ev.preventDefault(); const r = await api('/api/interacciones', { method: 'POST', body: formData(m) }); closeModal(); toast(r.primer_contacto ? 'Contacto nuevo registrado' : 'Seguimiento registrado'); route(); });
  });
});
window.editarPaso = guard(async oid => {
  const o = await api('/api/oportunidades/' + oid);
  modal('Próximo paso · ' + o.razon, `<form id="fp"><label>Próximo paso *</label><input name="proximo_paso" style="width:100%" value="${esc(o.proximo_paso || '')}"><label>Fecha *</label><input type="date" name="proxima_fecha" value="${o.proxima_fecha || addDias(1)}">${fechaChips()}
   <label><input type="checkbox" name="caliente" ${o.caliente ? 'checked' : ''} style="width:auto"> Marcar como caliente ★</label>
   <div class="row" style="margin-top:14px"><button class="btn primary">Guardar</button></div></form>`, m => {
    wireChips(m);
    $('#fp', m).onsubmit = guard(async ev => { ev.preventDefault(); await api('/api/oportunidades/' + oid, { method: 'PUT', body: formData(m) }); closeModal(); toast('Próximo paso actualizado'); route(); });
  });
});
window.agregarPipeline = guard(async eid => {
  const fac = await api('/api/empresas/facetas');
  modal('Agregar al pipeline', `<form id="fa"><input type="hidden" name="empresa_id" value="${eid}"><div class="f2"><div><label>Etapa inicial</label><select name="etapa">${CFG.activas.map(x => `<option>${x}</option>`).join('')}</select></div>
   <div><label>Valor estimado (COP)</label><input name="valor_estimado" inputmode="numeric" placeholder="0"></div><div><label>Responsable</label><input name="responsable" list="resp" placeholder="Nombre"><datalist id="resp">${fac.responsables.map(r => `<option>${esc(r)}</option>`).join('')}</datalist></div></div>
   <label>Próximo paso *</label><input name="proximo_paso" style="width:100%" value="Primer contacto"><label>Fecha *</label><input type="date" name="proxima_fecha" value="${hoyStr()}">${fechaChips()}
   <div class="row" style="margin-top:14px"><button class="btn primary">Agregar</button></div></form>`, m => {
    wireChips(m);
    $('#fa', m).onsubmit = guard(async ev => { ev.preventDefault(); await api('/api/oportunidades', { method: 'POST', body: formData(m) }); closeModal(); toast('Agregada al pipeline'); route(); });
  });
});

/* ============================ PIPELINE ============================ */
async function vPipeline() {
  const opps = await api('/api/oportunidades'); const fac = await api('/api/empresas/facetas');
  const resp = ST.resp || '';
  const list = opps.filter(o => !resp || o.responsable === resp);
  view.innerHTML = `<div class="row sp"><h1>Pipeline <span class="mut sm">${list.filter(o => !['Ganado', 'Perdido'].includes(o.etapa)).length} activas · ${COP(list.filter(o => !['Ganado', 'Perdido'].includes(o.etapa)).reduce((a, o) => a + (o.valor_estimado || 0), 0))} estimado</span></h1>
   <select id="rf"><option value="">Todos los responsables</option>${fac.responsables.map(r => `<option ${r === resp ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select></div>
   <div class="kan">${CFG.etapas.map(et => { const c = list.filter(o => o.etapa === et); return `<div class="col" data-e="${et}"><h3><span>${et}</span><span>${c.length}</span></h3>${c.map(card).join('') || '<div class="sm mut" style="padding:6px">—</div>'}</div>`; }).join('')}</div>
   <div class="sm mut">Arrastre las tarjetas entre columnas o use el selector “Mover”. Ganado/Perdido muestran los últimos 30 días.</div>`;
  $('#rf').onchange = e => { ST.resp = e.target.value; vPipeline(); };
  $$('.opp').forEach(c => { c.draggable = true; c.ondragstart = e => e.dataTransfer.setData('text/plain', c.dataset.id); });
  $$('.col').forEach(col => {
    col.ondragover = e => { e.preventDefault(); col.classList.add('over'); }; col.ondragleave = () => col.classList.remove('over');
    col.ondrop = e => { e.preventDefault(); col.classList.remove('over'); moverOpp(+e.dataTransfer.getData('text/plain'), col.dataset.e); };
  });
}
function card(o) {
  const cls = o.etapa === 'Ganado' || o.etapa === 'Perdido' ? '' : o.vencido ? 'venc' : o.sin_proximo_paso ? 'nopaso' : o.es_caliente ? 'hot' : '';
  const cerr = o.etapa === 'Ganado' ? `<div class="sm"><b>${COP(o.valor_cerrado)}</b> vendido · cobrado ${COP(o.cobrado)}${o.saldo > 0 ? ` · <span style="color:var(--bad)">saldo ${COP(o.saldo)}</span>` : ' ✓'}</div>` : '';
  return `<div class="opp ${cls}" data-id="${o.id}"><a class="t" href="#empresa/${o.empresa_id}">${esc(o.razon)}</a>
   <div class="sm mut">${tagP(o.prioridad)} ${o.valor_estimado && o.etapa !== 'Ganado' ? COP(o.valor_estimado) : ''} ${o.responsable ? '· ' + esc(o.responsable) : ''}</div>
   ${o.etapa === 'Ganado' ? cerr : o.etapa === 'Perdido' ? `<div class="sm mut">${esc(o.motivo_perdida || '')}</div>` : `<div class="sm">${o.proximo_paso ? '➜ ' + esc(o.proximo_paso) : '<span class="tag amb">Sin próximo paso</span>'}<br><span class="${o.vencido ? 'b' : 'mut'}" style="${o.vencido ? 'color:var(--bad)' : ''}">${fmtF(o.proxima_fecha)}</span>${o.ultima_interaccion ? ' <span class="mut">· últ. ' + fmtF(o.ultima_interaccion) + '</span>' : ''}</div>`}
   <div class="row" style="margin-top:6px;gap:4px"><select class="sm" onchange="moverOpp(${o.id},this.value)" style="padding:2px;max-width:105px"><option value="">Mover…</option>${CFG.etapas.filter(x => x !== o.etapa).map(x => `<option>${x}</option>`).join('')}</select>
   ${o.etapa === 'Ganado' ? `<button class="btn sm acc" onclick="cobro(${o.id})">+ Cobro</button>` : o.etapa === 'Perdido' ? '' : `<button class="btn sm" onclick="regContacto(${o.empresa_id},${o.id})">Contacto</button>`}</div></div>`;
}
window.moverOpp = guard(async (oid, etapa) => {
  if (!etapa) return; const o = await api('/api/oportunidades/' + oid);
  if (etapa === o.etapa) return;
  const abierta = !['Ganado', 'Perdido'].includes(o.etapa);
  if (etapa === 'Ganado') return modal('Cerrar venta · ' + o.razon, `<form id="fm"><label>Valor cerrado (COP) *</label><input name="valor_cerrado" inputmode="numeric" value="${o.valor_estimado || ''}"><label>Fecha de cierre</label><input type="date" name="fecha_cierre" value="${hoyStr()}">
    <div class="sm mut" style="margin-top:8px">El dinero cobrado se registra después, desde la tarjeta (“+ Cobro”).</div><div class="row" style="margin-top:14px"><button class="btn primary">Marcar como ganada</button></div></form>`, m => envia(m, oid, etapa));
  if (etapa === 'Perdido') return modal('Marcar como perdida · ' + o.razon, `<form id="fm"><label>Motivo (opcional)</label><input name="motivo" style="width:100%" placeholder="Precio, timing, competencia…"><div class="row" style="margin-top:14px"><button class="btn primary">Marcar como perdida</button></div></form>`, m => envia(m, oid, etapa));
  const necesita = !o.proximo_paso || !o.proxima_fecha || o.proxima_fecha < hoyStr() || !abierta;
  const body = { etapa, reabrir: !abierta };
  if (!necesita) { await api(`/api/oportunidades/${oid}/mover`, { method: 'POST', body }); toast('Movida a ' + etapa); return route(); }
  modal(`Mover a “${etapa}” · ${o.razon}`, `<form id="fm"><div class="note">Toda oportunidad activa necesita un próximo paso con fecha.</div><label>Próximo paso *</label><input name="proximo_paso" style="width:100%" value="${esc(o.proximo_paso || '')}"><label>Fecha *</label><input type="date" name="proxima_fecha" value="${o.proxima_fecha && o.proxima_fecha >= hoyStr() ? o.proxima_fecha : addDias(2)}">${fechaChips()}<div class="row" style="margin-top:14px"><button class="btn primary">Mover</button></div></form>`, m => { wireChips(m); envia(m, oid, etapa, body); });
});
function envia(m, oid, etapa, extra = {}) {
  $('#fm', m).onsubmit = guard(async ev => { ev.preventDefault(); await api(`/api/oportunidades/${oid}/mover`, { method: 'POST', body: { ...extra, ...formData(m), etapa } }); closeModal(); toast('Movida a ' + etapa); route(); });
}
window.cobro = guard(async oid => {
  const o = await api('/api/oportunidades/' + oid); const saldo = o.valor_cerrado - o.cobrado;
  modal('Registrar cobro · ' + o.razon, `<div class="sm">Vendido ${COP(o.valor_cerrado)} · Cobrado ${COP(o.cobrado)} · <b>Saldo ${COP(saldo)}</b></div>
   <form id="fc"><label>Monto cobrado (COP) *</label><input name="monto" inputmode="numeric" value="${saldo > 0 ? Math.round(saldo) : ''}"><label>Fecha</label><input type="date" name="fecha" value="${hoyStr()}"><label>Nota</label><input name="nota" style="width:100%" placeholder="Transferencia, factura #…">
   <div class="row" style="margin-top:14px"><button class="btn primary">Registrar cobro</button></div></form>
   ${o.cobros.length ? '<h2 style="margin-top:14px">Cobros previos</h2>' + o.cobros.map(c => `<div class="item"><div class="main">${COP(c.monto)} · ${fmtF(c.fecha)} <span class="mut sm">${esc(c.nota || '')}</span></div><button class="btn sm danger" onclick="delCobro(${c.id})">×</button></div>`).join('') : ''}`, m => {
    $('#fc', m).onsubmit = guard(async ev => { ev.preventDefault(); await api(`/api/oportunidades/${oid}/cobros`, { method: 'POST', body: formData(m) }); closeModal(); toast('Cobro registrado'); route(); });
  });
});
window.delCobro = guard(async id => { if (confirm('¿Eliminar este cobro?')) { await api('/api/cobros/' + id, { method: 'DELETE' }); closeModal(); route(); } });

/* ============================ SEGUIMIENTOS ============================ */
async function vSeg() {
  const v = ST.segv || 'vencidos';
  const [lst, hist] = await Promise.all([api('/api/seguimientos?vista=' + v), api('/api/interacciones')]);
  const tabs = [['vencidos', 'Vencidos'], ['hoy', 'Hoy'], ['proximos', 'Próximos 7 días'], ['sin_paso', 'Sin próximo paso'], ['todos', 'Todos']];
  view.innerHTML = `<h1>Seguimientos</h1><div class="tabs">${tabs.map(([k, l]) => `<button class="btn ${k === v ? 'on' : ''}" data-v="${k}">${l}</button>`).join('')}</div>
  <div class="card">${lst.length ? lst.map(o => oppItem(o)).join('') : '<div class="empty">Nada en esta vista.</div>'}</div>
  <div class="card"><h2>Contactos registrados (últimos 7 días)</h2>${hist.length ? `<div class="tw keep" style="border:0"><table><thead><tr><th>Fecha</th><th>Empresa</th><th>Tipo</th><th>Resultado</th><th>Notas</th></tr></thead><tbody>${hist.map(i => `<tr><td>${fmtF(i.fecha)}</td><td><a href="#empresa/${i.empresa_id}">${esc(i.razon)}</a></td><td>${esc(i.tipo)}</td><td>${esc(i.resultado)}</td><td class="sm">${esc(i.notas || '')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="sm mut">Aún no hay contactos registrados.</div>'}</div>`;
  $$('.tabs button').forEach(b => b.onclick = () => { ST.segv = b.dataset.v; vSeg(); });
}

/* ============================ DASHBOARD ============================ */
async function vDash() {
  const d = await api('/api/dashboard'); const m = d.metas, h = d.hoy;
  const kp = (l, a, b, tip) => `<div class="card kpi" title="${tip || ''}"><div class="l">${l}</div><div class="v">${a}<span class="mut sm"> / ${b}</span></div><div class="bar"><i class="${a >= b ? 'full' : ''}" style="width:${Math.min(100, b ? a / b * 100 : 0)}%"></i></div></div>`;
  const maxN = Math.max(1, ...d.embudo.map(x => x.n));
  view.innerHTML = `<div class="row sp"><h1>Dashboard comercial · hoy</h1><button class="btn" id="bm">Editar metas</button></div>
  <div class="grid g4">${kp('Contactos nuevos', h.contactos_nuevos, m.contactos_nuevos, 'Empresas contactadas por primera vez hoy')}${kp('Seguimientos', h.seguimientos, m.seguimientos, 'Contactos a empresas ya contactadas antes')}${kp('Propuestas enviadas', h.propuestas, m.propuestas)}${kp('Reuniones agendadas', h.reuniones, m.reuniones)}</div>
  <div class="grid g4" style="margin-top:12px">
   <div class="card kpi"><div class="l">Cobrado hoy</div><div class="v">${COP(h.cobrado)}</div><div class="sm mut">Referencia: ${COP(m.cobro_diario)} · ${m.cobro_diario ? Math.round(h.cobrado / m.cobro_diario * 100) : 0}% (es una meta, no un ingreso garantizado)</div></div>
   <div class="card kpi"><div class="l">Vendido hoy (cierres)</div><div class="v">${COP(h.vendido)}</div><div class="sm mut">${h.cierres} cierre(s) · distinto de lo cobrado</div></div>
   <div class="card kpi"><div class="l">Este mes</div><div class="v">${COP(d.mes.cobrado)}</div><div class="sm mut">cobrado · vendido ${COP(d.mes.vendido)}</div></div>
   <div class="card kpi"><div class="l">Por cobrar (ventas ganadas)</div><div class="v">${COP(d.ganadas.por_cobrar)}</div><div class="sm mut">de ${COP(d.ganadas.valor)} vendidos (${d.ganadas.n}) · cobrado ${COP(d.ganadas.cobrado)}</div></div></div>
  <div class="grid g2" style="margin-top:12px"><div class="card"><h2>Avance de oportunidades activas · ${d.activas.n} (${COP(d.activas.valor)} estimado)</h2>
   ${d.embudo.map(x => `<div style="margin:8px 0"><div class="row sp sm"><span>${x.etapa}</span><span><b>${x.n}</b> · ${COP(x.valor)}</span></div><div class="bar"><i style="width:${x.n / maxN * 100}%"></i></div></div>`).join('')}
   <div class="sm mut" style="margin-top:8px">Ganadas: ${d.ganadas.n} · Perdidas: ${d.perdidas} · Vencidas: <b style="color:${d.vencidos ? 'var(--bad)' : 'inherit'}">${d.vencidos}</b> · Sin próximo paso: <b style="color:${d.sin_paso ? 'var(--bad)' : 'inherit'}">${d.sin_paso}</b></div></div>
  <div class="card"><h2>Últimos 7 días</h2><div class="tw keep" style="border:0"><table><thead><tr><th>Día</th><th class="r">Nuevos</th><th class="r">Seg.</th><th class="r">Prop.</th><th class="r">Reun.</th><th class="r">Vendido</th><th class="r">Cobrado</th></tr></thead><tbody>${[...d.dias].reverse().map(x => `<tr><td>${fmtF(x.fecha)}</td><td class="r">${x.contactos_nuevos}</td><td class="r">${x.seguimientos}</td><td class="r">${x.propuestas}</td><td class="r">${x.reuniones}</td><td class="r">${COP(x.vendido)}</td><td class="r">${COP(x.cobrado)}</td></tr>`).join('')}</tbody></table></div></div></div>`;
  $('#bm').onclick = () => modal('Metas diarias (editables)', `<form id="fmt"><div class="f2"><div><label>Contactos nuevos</label><input name="contactos_nuevos" value="${m.contactos_nuevos}"></div><div><label>Seguimientos</label><input name="seguimientos" value="${m.seguimientos}"></div><div><label>Propuestas</label><input name="propuestas" value="${m.propuestas}"></div><div><label>Reuniones</label><input name="reuniones" value="${m.reuniones}"></div><div><label>Meta de ventas cerradas (COP)</label><input name="ventas_cerradas" value="${m.ventas_cerradas ?? 1000000}"></div><div><label>Meta de dinero cobrado (COP)</label><input name="cobro_diario" value="${m.cobro_diario}"></div></div><div class="row" style="margin-top:14px"><button class="btn primary">Guardar</button></div></form>`, mm => {
    $('#fmt', mm).onsubmit = guard(async ev => { ev.preventDefault(); await api('/api/metas', { method: 'PUT', body: formData(mm) }); closeModal(); toast('Metas actualizadas'); vDash(); });
  });
}

/* ============================ IMPORTAR ============================ */
async function vImportar() {
  const imps = await api('/api/importaciones');
  view.innerHTML = `<h1>Importar base de empresas</h1><div class="dz"><p><b>Suba un archivo CSV o Excel (.xlsx)</b></p><p class="sm mut">Se detectan las columnas automáticamente. Nunca se sobrescriben empresas existentes ni se borra información: los duplicados y errores quedan en un reporte descargable.</p><input type="file" id="fi" accept=".csv,.txt,.xlsx,.xlsm"></div><div id="res"></div>
  <div class="card" style="margin-top:14px"><h2>Importaciones anteriores</h2>${imps.length ? `<table><thead><tr><th>Archivo</th><th>Fecha</th><th class="r">Filas</th><th class="r">Nuevas</th><th class="r">Duplicadas</th><th class="r">Errores</th><th class="r">Por revisar</th><th></th></tr></thead><tbody>${imps.map(i => `<tr><td>${esc(i.archivo)}</td><td class="sm">${esc(i.fecha)}</td><td class="r">${NUM(i.total)}</td><td class="r">${NUM(i.nuevas)}</td><td class="r">${i.duplicadas}</td><td class="r">${i.errores}</td><td class="r">${NUM(i.revisar)}</td><td><a href="/api/importaciones/${i.id}/reporte.csv">Reporte CSV</a></td></tr>`).join('')}</tbody></table>` : '<div class="sm mut">Sin importaciones aún.</div>'}</div>`;
  $('#fi').onchange = guard(async e => {
    const file = e.target.files[0]; if (!file) return;
    $('#res').innerHTML = '<div class="card">Analizando…</div>';
    const fd = new FormData(); fd.append('archivo', file); fd.append('preview', '1');
    const p = await api('/api/importar', { method: 'POST', body: fd });
    $('#res').innerHTML = `<div class="card"><h2>Vista previa (aún no se ha importado nada)</h2><div class="grid g4"><div class="kpi"><div class="l">Filas</div><div class="v">${NUM(p.filas)}</div></div><div class="kpi"><div class="l">Empresas nuevas</div><div class="v" style="color:var(--ok)">${NUM(p.nuevas)}</div></div><div class="kpi"><div class="l">Duplicadas (se omiten)</div><div class="v">${NUM(p.duplicadas)}</div></div><div class="kpi"><div class="l">Errores (se omiten)</div><div class="v" style="color:var(--bad)">${NUM(p.errores)}</div></div></div>
     <p class="sm">${NUM(p.para_revisar)} empresa(s) se importarán marcadas “por revisar”. Columnas detectadas: ${Object.entries(p.columnas_detectadas).map(([k, v]) => `<span class="tag">${k} ← ${esc(v.slice(0, 40))}</span>`).join(' ')}</p>
     <button class="btn primary" id="go">Importar ${NUM(p.nuevas)} empresas válidas</button></div>`;
    $('#go').onclick = guard(async () => { $('#go').disabled = true; $('#go').textContent = 'Importando…'; fd.set('preview', '0'); const r = await api('/api/importar', { method: 'POST', body: fd }); toast(`Importadas ${r.nuevas} empresas`); vImportar(); });
  });
}

window.addEventListener('load', () => route());
