const el = (tag, attrs={}, children=[]) => {
  const n = document.createElement(tag);
  Object.entries(attrs).forEach(([k,v]) => {
    if(k === 'class') n.className = v;
    else if(k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2).toLowerCase(), v);
    else if(v !== undefined && v !== null) n.setAttribute(k, v);
  });
  (Array.isArray(children) ? children : [children]).forEach(c => {
    if(c === null || c === undefined) return;
    if(typeof c === 'string') n.appendChild(document.createTextNode(c));
    else n.appendChild(c);
  });
  return n;
};

const fmt = (n) => {
  if(n === null || n === undefined || Number.isNaN(Number(n))) return '0';
  const s = Number(n).toFixed(4).replace(/0+$/,'').replace(/\.$/,'');
  return s.replace('.', ',');
};

const state = {
  route: '/',
  projects: [],
  project: null,
  measures: {},
  variant: {},
  clientName: '',
  calc: null,
  showBreakdown: false,
  adminPassword: localStorage.getItem('ponticelli_admin_pwd') || '',
};

async function api(path, opts={}){
  const headers = Object.assign({'Content-Type':'application/json'}, opts.headers||{});
  if(state.adminPassword) headers['X-Admin-Password'] = state.adminPassword;
  const res = await fetch(path, Object.assign({}, opts, {headers}));
  const data = await res.json().catch(() => ({}));
  if(!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

async function loadProjects(){
  const {projects} = await api('/api/projects');
  state.projects = projects;
}

function mount(node){
  const root = document.getElementById('app');
  root.innerHTML = '';
  root.appendChild(node);
}

function Steps(active){
  return el('div', {class:'steps'}, [
    el('div', {class:'step ' + (active===1?'active':'')}, '1) Proyecto'),
    el('div', {class:'step ' + (active===2?'active':'')}, '2) Con qué + medidas'),
    el('div', {class:'step ' + (active===3?'active':'')}, '3) Resultado + PDF'),
  ]);
}

function Home(){
  const card = el('div', {class:'card'}, [
    Steps(1),
    el('div', {class:'h1'}, 'Elegí el proyecto'),
    el('div', {class:'p'}, 'Hacelo en 3 pasos. Vos solo elegís el proyecto y cargás medidas mínimas.'),
  ]);

  const list = el('div', {class:'grid'}, []);
  state.projects.forEach(p => {
    list.appendChild(
      el('button', {class:'btn', onClick: () => {
        state.project = p;
        state.measures = {};
        state.variant = {};
        state.calc = null;
        location.hash = '#/paso2';
      }}, `${p.name}`)
    );
  });
  card.appendChild(list);

  mount(card);
}

function field(labelText, id, placeholder, type='number'){
  const input = el('input', {class:'input', id, placeholder, type, step:'any'});
  input.value = state.measures[id] ?? '';
  input.addEventListener('input', () => {
    const v = input.value;
    state.measures[id] = v === '' ? '' : Number(v);
  });
  return el('div', {}, [
    el('label', {for:id}, labelText),
    input,
  ]);
}

function materialPicker(labelText, key){
  const wrap = el('div', {}, [el('label', {}, labelText)]);
  const row = el('div', {class:'row'}, []);
  const q = el('input', {class:'input', placeholder:'Buscar material... (ej: chapa cincalum, caño 40x40, lana de vidrio)'});
  q.style.flex = '1';
  const btn = el('button', {class:'btn small', onClick: async () => {
    try{
      const query = q.value.trim();
      if(!query) return;
      const data = await api('/api/materials?q=' + encodeURIComponent(query));
      select.innerHTML = '';
      data.materials.forEach(m => {
        const opt = el('option', {value:m.id}, `${m.name} · ${m.saleUnit || m.uom || ''}`);
        select.appendChild(opt);
      });
      if(data.materials[0]){
        select.value = data.materials[0].id;
        state.variant[key] = data.materials[0].id;
      }
    }catch(e){
      alert(e.message);
    }
  }}, 'Buscar');

  const select = el('select', {class:'input'});
  select.addEventListener('change', () => { state.variant[key] = select.value; });

  row.appendChild(q);
  row.appendChild(btn);
  wrap.appendChild(row);
  wrap.appendChild(select);

  // valor previo
  if(state.variant[key]){
    const opt = el('option', {value:state.variant[key]}, 'Seleccionado: ' + state.variant[key]);
    select.appendChild(opt);
    select.value = state.variant[key];
  }else{
    const opt = el('option', {value:''}, 'Buscá y elegí un material');
    select.appendChild(opt);
  }

  return wrap;
}

function Paso2(){
  if(!state.project){ location.hash = '#/'; return; }

  const p = state.project;

  const card = el('div', {class:'card'}, [
    Steps(2),
    el('div', {class:'h1'}, 'Con qué + medidas'),
    el('div', {class:'p'}, 'Elegí la variante principal del proyecto y cargá solo las medidas mínimas.'),
    el('div', {class:'badge ok'}, `Proyecto: ${p.name}`),
    el('div', {class:'hr'}),
  ]);

  const form = el('div', {class:'grid two'}, []);

  // Campos por tipo
  if(p.type === 'roof_sheet'){
    form.appendChild(materialPicker('Chapa principal', 'sheetMaterialId'));
    form.appendChild(field('Largo (m) ej: 6', 'largo_m', '6'));
    form.appendChild(field('Ancho (m) ej: 3', 'ancho_m', '3'));

    const ew = el('input', {class:'input', placeholder:'Ancho efectivo (m) ej: 1', type:'number', step:'any'});
    ew.value = state.variant.effectiveWidthM ?? '';
    ew.addEventListener('input', () => state.variant.effectiveWidthM = ew.value===''? '' : Number(ew.value));
    form.appendChild(el('div', {}, [el('label', {}, 'Ancho efectivo (m) (opcional)'), ew]));

  } else if(p.type === 'drywall'){
    form.appendChild(materialPicker('Placa / placa principal', 'boardMaterialId'));
    form.appendChild(field('Largo (m) ej: 3', 'largo_m', '3'));
    form.appendChild(field('Alto (m) ej: 2.4', 'alto_m', '2.4'));

  } else {
    // metal_frame por defecto
    form.appendChild(materialPicker('Perfil principal', 'profileMaterialId'));
    form.appendChild(field('Ancho (m) ej: 3', 'ancho_m', '3'));
    form.appendChild(field('Alto (m) ej: 2', 'alto_m', '2'));

    const t = el('input', {class:'input', placeholder:'Travesaños (un) ej: 2', type:'number', step:'1'});
    t.value = state.measures.travesanos ?? '';
    t.addEventListener('input', () => state.measures.travesanos = t.value===''? '' : Number(t.value));
    form.appendChild(el('div', {}, [el('label', {}, 'Travesaños (opcional)'), t]));

    // Este proyecto puede usar consumibles, pero el cliente NO los ingresa.
    // Si el admin quiere mapear IDs específicos de electrodos/discos, lo puede setear en el JSON.
  }

  card.appendChild(form);

  const actions = el('div', {class:'row', style:'margin-top:14px'}, [
    el('button', {class:'btn', onClick: () => { location.hash = '#/'; }}, '← Cambiar proyecto'),
    el('button', {class:'btn primary', onClick: async () => {
      try{
        // Validación mínima: requeridos según tipo
        const req = [];
        if(p.type === 'roof_sheet'){ req.push(['sheetMaterialId','Elegí una chapa']); req.push(['largo_m','Cargá largo']); req.push(['ancho_m','Cargá ancho']); }
        else if(p.type === 'drywall'){ req.push(['boardMaterialId','Elegí una placa']); req.push(['largo_m','Cargá largo']); req.push(['alto_m','Cargá alto']); }
        else { req.push(['profileMaterialId','Elegí un perfil']); req.push(['ancho_m','Cargá ancho']); req.push(['alto_m','Cargá alto']); }

        for(const [k,msg] of req){
          const v = (k in state.variant) ? state.variant[k] : state.measures[k];
          if(v === '' || v === null || v === undefined){ throw new Error(msg); }
          if(typeof v === 'number' && !(v>0)) throw new Error(msg);
        }

        const data = await api('/api/calc', {method:'POST', body: JSON.stringify({projectId: p.id, variant: state.variant, measures: state.measures})});
        state.calc = data.result;
        location.hash = '#/resultado';
      }catch(e){
        alert(e.message);
      }
    }}, 'Ver resultado →')
  ]);

  card.appendChild(actions);

  mount(card);
}

function Resultado(){
  if(!state.project || !state.calc){ location.hash = '#/'; return; }

  const p = state.project;

  const card = el('div', {class:'card'}, [
    Steps(3),
    el('div', {class:'h1'}, 'Resultado'),
    el('div', {class:'p'}, 'Te mostramos cantidad exacta y cantidad a comprar según unidad de venta. Minimiza sobrante donde aplique.'),
    el('div', {class:'row'}, [
      el('span', {class:'badge ok'}, `Proyecto: ${p.name}`),
      el('span', {class:'badge'}, `Ítems: ${state.calc.lines.length}`),
    ]),
    el('div', {class:'hr'}),
  ]);

  // Tabla
  const table = el('table', {class:'table'}, [
    el('thead', {}, el('tr', {}, [
      el('th', {}, 'Material'),
      el('th', {}, 'Unidad'),
      el('th', {}, 'Cantidad exacta'),
      el('th', {}, 'Unidad de venta'),
      el('th', {}, 'Cantidad a comprar'),
      el('th', {}, 'Observaciones'),
    ])),
  ]);

  const tbody = el('tbody');
  state.calc.lines.forEach(r => {
    tbody.appendChild(el('tr', {}, [
      el('td', {}, r.material),
      el('td', {class:'muted'}, r.unidad || ''),
      el('td', {}, fmt(r.cantidadExacta)),
      el('td', {class:'muted'}, r.unidadVenta || ''),
      el('td', {}, fmt(r.cantidadComprar)),
      el('td', {class:'muted'}, r.observaciones || ''),
    ]));
  });

  table.appendChild(tbody);
  card.appendChild(table);

  // Ver cómo se calculó
  const toggle = el('button', {class:'btn', onClick: () => { state.showBreakdown = !state.showBreakdown; render(); }}, state.showBreakdown ? 'Ocultar cómo se calculó' : 'Ver cómo se calculó');
  card.appendChild(el('div', {class:'row', style:'margin-top:14px'}, [toggle]));

  if(state.showBreakdown){
    const b = (state.calc.breakdown || []).map(x => '• ' + x).join('\n');
    card.appendChild(el('div', {class:'code', style:'margin-top:10px'}, b || 'Sin desglose'));
  }

  // Cliente + PDF
  const client = el('input', {class:'input', placeholder:'Nombre / Empresa del cliente (para el PDF)'});
  client.value = state.clientName;
  client.addEventListener('input', () => state.clientName = client.value);

  const pdfBtn = el('button', {class:'btn primary', onClick: async () => {
    try{
      const data = await api('/api/pdf', {method:'POST', body: JSON.stringify({clientName: state.clientName, projectName: p.name, calc: state.calc})});
      window.open(data.url, '_blank');
    }catch(e){
      alert(e.message);
    }
  }}, 'Descargar PDF');

  card.appendChild(el('div', {class:'hr'}));
  card.appendChild(el('div', {class:'grid two'}, [
    el('div', {}, [el('label', {}, 'Cliente / Empresa'), client]),
    el('div', {style:'display:flex;align-items:end;justify-content:flex-end'}, pdfBtn),
  ]));

  // Supuestos
  const assumptions = state.calc.assumptions || [];
  if(assumptions.length){
    card.appendChild(el('div', {class:'hr'}));
    card.appendChild(el('div', {class:'notice'}, [
      el('div', {style:'font-weight:800;margin-bottom:6px'}, 'Supuestos editables'),
      el('div', {class:'muted'}, assumptions.map(x => '• ' + x).join('\n')),
    ]));
  }

  card.appendChild(el('div', {class:'row', style:'margin-top:14px'}, [
    el('button', {class:'btn', onClick: () => { location.hash = '#/paso2'; }}, '← Volver'),
    el('button', {class:'btn danger', onClick: () => {
      state.project = null; state.measures = {}; state.variant = {}; state.calc = null; state.showBreakdown=false;
      location.hash = '#/';
    }}, 'Nuevo cálculo')
  ]));

  mount(card);
}

function Admin(){
  const card = el('div', {class:'card'}, [
    el('div', {class:'h1'}, 'Admin'),
    el('div', {class:'p'}, 'Modo local protegido por clave (ENV ADMIN_PASSWORD). Permite ver cobertura y editar parámetros del JSON.'),
  ]);

  const pwd = el('input', {class:'input', placeholder:'Clave admin', type:'password'});
  pwd.value = state.adminPassword;
  pwd.addEventListener('input', () => {
    state.adminPassword = pwd.value;
    localStorage.setItem('ponticelli_admin_pwd', state.adminPassword);
  });

  const status = el('div', {class:'badge'}, 'Estado: sin verificar');

  const loadBtn = el('button', {class:'btn primary', onClick: async () => {
    try{
      const data = await api('/api/admin/coverage');
      const cov = data.coverage;
      status.className = 'badge ' + (cov.faltantes_count === 0 ? 'ok' : 'warn');
      status.textContent = cov.faltantes_count === 0
        ? `Cobertura OK · B_total=${cov.B_total} · FALTANTES=0`
        : `ATENCIÓN · FALTANTES=${cov.faltantes_count}`;

      report.textContent = JSON.stringify(cov, null, 2);
    }catch(e){
      status.className = 'badge warn';
      status.textContent = 'No autorizado o admin deshabilitado';
      report.textContent = e.message;
    }
  }}, 'Ver cobertura');

  const report = el('div', {class:'code', style:'margin-top:10px'}, '');

  // Editor simple para reglas
  const rulesWrap = el('div', {style:'margin-top:12px'});
  const rulesTitle = el('div', {style:'font-weight:800;margin:10px 0 6px'}, 'Editar parámetros (JSON)');
  const textarea = el('textarea', {class:'input', style:'min-height:220px', placeholder:'Cargar config para editar...'});

  const loadConfigBtn = el('button', {class:'btn', onClick: async () => {
    try{
      const data = await api('/api/admin/config');
      textarea.value = JSON.stringify(data.config, null, 2);
    }catch(e){
      alert(e.message);
    }
  }}, 'Cargar config');

  const saveConfigBtn = el('button', {class:'btn primary', onClick: async () => {
    try{
      const cfg = JSON.parse(textarea.value);
      const data = await api('/api/admin/config', {method:'POST', body: JSON.stringify({config: cfg})});
      alert(data.coverage.faltantes_count === 0 ? 'Guardado. Cobertura OK (FALTANTES=0).' : 'Guardado, pero hay faltantes.');
    }catch(e){
      alert('Error: ' + e.message);
    }
  }}, 'Guardar config');

  rulesWrap.appendChild(rulesTitle);
  rulesWrap.appendChild(el('div', {class:'row'}, [loadConfigBtn, saveConfigBtn]));
  rulesWrap.appendChild(textarea);

  card.appendChild(el('div', {class:'grid two'}, [
    el('div', {}, [el('label', {}, 'Clave admin'), pwd]),
    el('div', {style:'display:flex;align-items:end;justify-content:flex-end;gap:10px'}, [status, loadBtn]),
  ]));
  card.appendChild(report);
  card.appendChild(rulesWrap);

  mount(card);
}

function Router(){
  const hash = location.hash.replace('#','') || '/';
  state.route = hash;
  render();
}

function render(){
  if(state.route === '/admin') return Admin();
  if(state.route === '/paso2') return Paso2();
  if(state.route === '/resultado') return Resultado();
  return Home();
}

window.addEventListener('hashchange', Router);

(async function init(){
  try{
    const health = await api('/api/health');
    if(!health.ok) throw new Error('Health check falló');
    await loadProjects();
    Router();
  }catch(e){
    mount(el('div', {class:'card'}, [
      el('div', {class:'h1'}, 'No se pudo iniciar la app'),
      el('div', {class:'error'}, e.message),
      el('div', {class:'p'}, 'Revisá que exista el logo y el config.'),
    ]));
  }
})();
