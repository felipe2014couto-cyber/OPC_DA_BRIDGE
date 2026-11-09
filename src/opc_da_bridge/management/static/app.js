"use strict";

// The browser manages configuration only. All displayed samples come from the
// backend's explicitly identified Linux/Windows simulated source; no UA integration.
const ICONS = {
  bridge:'<path d="M3 17h18M5 17V8h14v9M2 8h20M8 8V4h8v4M9 12h6"/>',
  connection:'<rect x="8" y="2" width="8" height="5" rx="1"/><path d="M12 7v5M4 12h16M4 12v4m8-4v4m8-4v4"/><rect x="1" y="16" width="6" height="5" rx="1"/><rect x="9" y="16" width="6" height="5" rx="1"/><rect x="17" y="16" width="6" height="5" rx="1"/>',
  machine:'<rect x="2" y="3" width="20" height="14" rx="1"/><path d="M8 21h8m-4-4v4"/>',
  module:'<rect x="3" y="2" width="18" height="20" rx="1"/><path d="M9 2v20m6-20v20M5.5 6h1m4.5 0h2m4 0h1M5.5 11h1m4.5 0h2m4 0h1M5.5 16h1m4.5 0h2m4 0h1"/>',
  folder:'<path d="M2 6a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2v11H2Z"/>',
  tag:'<path d="m3 3 9 0 9 9-9 9-9-9Z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  play:'<path d="m7 3 14 9-14 9Z" fill="currentColor" stroke="none"/>',
  stop:'<rect x="4" y="4" width="16" height="16" rx="1" fill="currentColor" stroke="none"/>',
  refresh:'<path d="M21 9a9 9 0 0 0-15-6L3 6m0-5v5h5M3 15a9 9 0 0 0 15 6l3-3m0 5v-5h-5"/>',
  clipboard:'<rect x="5" y="4" width="15" height="18" rx="1"/><rect x="9" y="2" width="7" height="5" rx="1"/><path d="M9 11h7m-7 4h7m-7 4h5"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  filter:'<path d="M2 3h20l-8 9v8l-4 2V12Z"/>',
  shield:'<path d="m12 2 9 3v7c0 5-9 10-9 10S3 17 3 12V5Z"/><path d="m8 12 3 3 5-6"/>',
  edit:'<path d="m3 16 12-12 5 5-12 12H3Z"/><path d="m13 6 5 5M3 16l5 5"/>',
  trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 16h12l1-16M10 10v8m4-8v8"/>',
  eye:'<path d="M1 12S5 5 12 5s11 7 11 7-4 7-11 7S1 12 1 12Z"/><circle cx="12" cy="12" r="3"/>',
  monitor:'<path d="M3 3v18h18M4 14l4-5 4 8 4-11 5 3"/>',
  plus:'<path d="M12 3v18M3 12h18"/>',
  check:'<path d="m4 12 5 5L21 5"/>'
};
const $ = id => document.getElementById(id);
const model = {state:null,nodes:new Map(),selected:null,variable:null,collapsed:new Set(),samples:new Map(),failed:new Set(),monitored:new Set(),connected:false,readGeneration:0,events:[],eventCursor:0,busy:false,polling:false,lastFocus:null,modal:null};
const kindMeta = {
  connections:{label:"conexão",title:"Nova conexão",icon:"connection",child:"machines"},
  machines:{label:"máquina de destino",title:"Nova máquina",icon:"machine",parent:"connections",child:"modules"},
  modules:{label:"módulo de origem",title:"Novo módulo",icon:"module",parent:"machines",child:"folders"},
  folders:{label:"pasta",title:"Nova pasta",icon:"folder",parent:"modules",child:"variables"},
  variables:{label:"variável",title:"Nova variável",icon:"tag",parent:"folders"}
};
function icon(name) {const span=document.createElement("span");span.className="node-icon";span.innerHTML=`<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]||ICONS.tag}</svg>`;return span;}
function hydrateIcons(root=document) {root.querySelectorAll("[data-icon]").forEach(el=>{el.replaceChildren(icon(el.dataset.icon).firstChild);});}
function textEl(tag,text,className) {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function node(id){return model.nodes.get(id)||null;}
function ancestors(n){const a=[];while(n){a.unshift(n);n=node(n.parent);}return a;}
function current(){return node(model.variable)||node(model.selected);}
function ancestor(kind,from=current()){return ancestors(from).find(n=>n.kind===kind)||null;}
function folder(){return ancestor("folders",node(model.selected));}
function allVariables(){return [...model.nodes.values()].filter(n=>n.kind==="variables");}
function buildNodes(){model.nodes.clear();function walk(items,kind,parent){for(const data of items||[]){const n={...data,kind,parent};model.nodes.set(n.id,n);const child=kindMeta[kind].child;if(child)walk(data[child],child,n.id);}}walk(model.state?.connections,"connections",null);}
function notify(message){$("notice-message").textContent=message;$("notice").hidden=false;}
function clearNotice(){$("notice").hidden=true;}
class ApiError extends Error {constructor(message,status,payload){super(message);this.status=status;this.payload=payload;}}
async function api(path,options={}){
  const headers={Accept:"application/json",...(options.headers||{})};
  if(options.body!==undefined)headers["Content-Type"]="application/json";
  let response;try{response=await fetch(`/api/v1${path}`,{...options,headers,body:options.body===undefined?undefined:JSON.stringify(options.body)});}catch(_){throw new ApiError("Leitura indisponível: não foi possível acessar o backend de gerenciamento.",0,null);}
  let data;try{data=await response.json();}catch(_){throw new ApiError("O backend retornou uma resposta inválida.",response.status,null);}
  if(!response.ok){const message=typeof data.error==="string"?data.error:data.error?.message||data.message||"A operação não pôde ser concluída.";throw new ApiError(message,response.status,data);}
  return data;
}
function applyState(state){
  if(model.state&&model.state.revision!==state.revision){model.readGeneration++;model.samples.clear();model.failed.clear();}
  model.state=state;buildNodes();
  if(!node(model.selected)){model.selected=[...model.nodes.values()].find(n=>n.kind==="folders")?.id||state.connections?.[0]?.id||null;}
  if(!node(model.variable))model.variable=null;
  for(const id of [...model.monitored])if(!node(id))model.monitored.delete(id);
  renderTree();renderContent();updateActions();
}
async function loadState(showError=true){try{applyState(await api("/state"));return true;}catch(error){if(showError)notify(error.message);$("save-status").textContent="Backend indisponível";return false;}}
async function startPersistentLabSnapshot(){
 const profile=model.state?.lab_snapshot;
 if(!profile?.enabled||!Array.isArray(profile.bindings))return;
 const ids=profile.bindings.map(binding=>binding.variable_id).filter(id=>node(id)?.kind==="variables");
 if(!ids.length)return;
 for(const id of ids)model.monitored.add(id);
 await readSamples(ids,true);
}
function childCount(n){const child=kindMeta[n.kind].child;return child?(n[child]||[]).length:0;}
function renderTree(){
  const tree=$("tree");tree.replaceChildren();$("tree-total").textContent=String(model.state?.connections?.length||0);
  if(!model.state?.connections?.length){const empty=textEl("div",undefined,"tree-empty");empty.append(textEl("p","Nenhuma conexão cadastrada. Comece criando a estrutura do laboratório."));const button=textEl("button","Nova conexão","primary-button");button.addEventListener("click",()=>openEditor("connections"));empty.append(button);tree.append(empty);return;}
  function render(items,kind,depth){for(const data of items||[]){const n=node(data.id);const row=textEl("div",undefined,"tree-row");row.dataset.nodeKind=kind;row.dataset.nodeId=n.id;row.dataset.testid=`tree-node-${n.id}`;row.style.paddingLeft=`${depth*19}px`;row.setAttribute("role","treeitem");row.setAttribute("tabindex","0");row.setAttribute("aria-level",String(depth+1));row.setAttribute("aria-selected",String(model.selected===n.id));if(model.selected===n.id)row.classList.add("selected");const child=kindMeta[kind].child;const expandable=kind!=="folders"&&childCount(n)>0;const toggle=textEl("button",model.collapsed.has(n.id)?"▸":"▾","tree-toggle");toggle.tabIndex=-1;toggle.setAttribute("aria-label",model.collapsed.has(n.id)?"Desenvolver":"Recolher");if(!expandable)toggle.classList.add("invisible");else row.setAttribute("aria-expanded",String(!model.collapsed.has(n.id)));toggle.addEventListener("click",e=>{e.stopPropagation();model.collapsed.has(n.id)?model.collapsed.delete(n.id):model.collapsed.add(n.id);renderTree();});row.append(toggle,icon(kindMeta[kind].icon),textEl("span",n.name,"node-label"));if(kind==="folders")row.append(textEl("span",String(childCount(n)),"node-count"));row.addEventListener("click",()=>selectNode(n.id));row.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();selectNode(n.id);}if(e.key==="F2"){e.preventDefault();openEditor(kind,n);}});row.addEventListener("contextmenu",e=>{e.preventDefault();selectNode(n.id);showMenu([{label:"Editar",icon:"edit",testid:"context-edit",action:()=>openEditor(kind,n)},{label:"Excluir",icon:"trash",testid:"context-delete",danger:true,action:()=>confirmDelete(n)}],e.clientX,e.clientY);});tree.append(row);if(expandable&&!model.collapsed.has(n.id))render(data[child],child,depth+1);}}
  render(model.state.connections,"connections",0);
}
function selectNode(id){model.selected=id;model.variable=null;closeMenu();renderTree();renderContent();updateActions();}
function selectVariable(id){model.variable=id;renderTable();renderDetails();updateActions();}
function renderContent(){
  const selected=node(model.selected),f=folder();const crumbs=$("breadcrumb");crumbs.replaceChildren(icon(f?"folder":selected?kindMeta[selected.kind].icon:"folder"));
  const chain=ancestors(selected);if(!chain.length)crumbs.append(textEl("span","Selecione uma pasta"));else chain.forEach((n,i)=>{if(i)crumbs.append(textEl("span","›","breadcrumb-separator"));crumbs.append(textEl("span",n.name,i===chain.length-1?"breadcrumb-last":""));});
  $("variables-title").textContent=f?`Variáveis OPC DA (${f.name})`:"Variáveis OPC DA";
  $("variable-count").textContent=`${f?.variables?.length||0} ${(f?.variables?.length||0)===1?"variável":"variáveis"}`;
  const empty=$("empty-state"),hasRows=!!f?.variables?.length;empty.hidden=hasRows;
  if(!hasRows){empty.replaceChildren();const symbol=textEl("div",undefined,"empty-symbol");symbol.append(icon(selected?kindMeta[selected.kind].icon:"connection"));empty.append(symbol);let title="Organize sua configuração",copy="Crie uma conexão, adicione uma máquina de destino e organize as variáveis em módulos e pastas.",create="connections",buttonLabel="Criar conexão";
    if(f){title="Esta pasta ainda não tem variáveis";copy=ancestor("modules",f)?.source_kind==="opc_da"?"Cadastre os Item IDs de origem e publicado, preservando os tipos observados no servidor.":"Cadastre uma variável Real, Inteiro, String ou Booleano e defina seu valor inicial simulado.";create="variables";buttonLabel="Nova variável";}
    else if(selected){const child=kindMeta[selected.kind].child;title=`${selected.name}`;create=child||"connections";buttonLabel=kindMeta[create]?.title||"Nova conexão";copy=selected.kind==="machines"?`Destino: ${selected.host}. Confira a identidade e a aplicação no painel do agente Windows.`:selected.kind==="modules"?`Origem: ${selected.source_kind==="opc_da"?`${selected.source_host} · ${selected.source_progid}`:"simulada"}. Selecione ou crie uma pasta.`:"Selecione uma pasta na árvore ou continue criando os níveis da configuração.";}
    empty.append(textEl("h3",title),textEl("p",copy));const button=textEl("button",buttonLabel,"primary-button");button.addEventListener("click",()=>openEditor(create));empty.append(button);
  }
  renderTable();renderDetails();updateStatus();
}
function displayedSample(v){return model.connected?model.samples.get(v.id):null;}
function sourceReadMode(v){const mod=ancestor("modules",v);if(mod?.source_kind!=="opc_da")return null;const m=ancestor("machines",mod);return m?.sourceState?.origins?.find(o=>o.module_id===mod.id)||null;}
function sampleOriginLabel(v,s){const mode=sourceReadMode(v);if(s?.lab_snapshot||mode?.transport==="rust_lab_loopback_snapshot")return "Snapshot de laboratório · Fonte simulada";if(mode?.source_environment==="real_read_only")return mode.status==="paused"?"Origem OPC DA real · coleta pausada":"Leitura OPC DA real · somente leitura";if(s?.source_conversion==="float_to_double")return "Leitura OPC DA de laboratório · Float (VT_R4) → Double (VT_R8) · Fonte simulada";if(mode?.source_environment==="laboratory"&&s?.source_kind==="opc_da")return "Leitura OPC DA de laboratório · Fonte simulada";return s?.origin||"Sem leitura";}
function quality(v){const s=displayedSample(v);if(model.failed.has(v.id))return {label:"Indisponível",css:"bad"};if(!s)return {label:"Sem leitura",css:""};return {label:(s.quality_code&192)===192?"Boa":(s.quality_code&192)===0?"Ruim":"Incerta",css:(s.quality_code&192)===192?"good":"bad"};}
function formatValue(sample){if(!sample||sample.value===null||sample.value===undefined)return "—";if(typeof sample.value==="boolean")return sample.value?"True":"False";if(typeof sample.value==="number")return sample.value.toLocaleString("pt-BR",{maximumFractionDigits:10});return String(sample.value);}
function formatTimestamp(value){if(!value)return "Ausente";const d=new Date(value);return Number.isNaN(d.getTime())?"Timestamp inválido":d.toLocaleString("pt-BR");}
function renderTable(){const body=$("variables-body");body.replaceChildren();for(const data of [...(folder()?.variables||[])].sort((a,b)=>a.item_id.localeCompare(b.item_id))){const v=node(data.id),s=displayedSample(v),q=quality(v);const row=document.createElement("tr");row.dataset.testid=`variable-row-${v.id}`;row.dataset.variableId=v.id;row.tabIndex=0;row.setAttribute("aria-selected",String(model.variable===v.id));if(model.variable===v.id)row.classList.add("selected");const name=textEl("td");const inner=textEl("div",undefined,"variable-name");inner.append(icon("tag"),textEl("span",v.item_id));if(model.monitored.has(v.id)){const marker=textEl("span","∿","monitor-indicator");marker.title="Consulta periódica do cache ativo";inner.append(marker);}name.append(inner);name.title=v.item_id;row.append(name);
    for(const text of [v.source_id,v.data_type]){const cell=textEl("td",text||"—");cell.title=text||"";row.append(cell);}
    const qc=textEl("td"),ql=textEl("span",undefined,"quality-cell");ql.append(textEl("span",undefined,`quality-dot ${q.css}`),textEl("span",q.label));qc.append(ql);row.append(qc);const desc=textEl("td",v.description||"—");desc.title=v.description||"";row.append(desc,textEl("td",formatValue(s),"value-cell"));row.addEventListener("click",()=>selectVariable(v.id));row.addEventListener("dblclick",()=>readVariable(v.id));row.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();readVariable(v.id);}if(e.key==="F2"){e.preventDefault();openEditor("variables",v);}});row.addEventListener("contextmenu",e=>{e.preventDefault();selectVariable(v.id);variableMenu(v,e.clientX,e.clientY);});body.append(row);}}
function renderDetails(){const panel=$("variable-details"),v=node(model.variable);panel.hidden=!v;if(!v)return;const s=displayedSample(v),q=quality(v),sourceMode=sourceReadMode(v),labSnapshot=!!s?.lab_snapshot||sourceMode?.transport==="rust_lab_loopback_snapshot",labOpc=sourceMode?.source_environment==="laboratory"&&s?.source_kind==="opc_da";panel.replaceChildren();const top=textEl("div",undefined,"details-top");const badge=labSnapshot?"Snapshot de laboratório · Fonte simulada":labOpc?"Leitura OPC DA de laboratório · Fonte simulada":s?.source_kind==="opc_da"?"Cache Windows · OPC DA":s?.applied_to_windows?"Cache Windows · fonte simulada":(ancestor("modules",v)?.source_kind==="opc_da"?"OPC DA · sem amostra confirmada":"Prévia Linux · fonte simulada");top.append(icon("tag"),textEl("span",v.name),textEl("span",badge,"small-sim-badge"));panel.append(top);const grid=textEl("div",undefined,"details-grid");const valueLabel=labSnapshot?(s?.snapshot_status==="unavailable"?"Valor atual:":"Valor:"):s?.source_kind==="opc_da"&&(s.hresult<0||(s.quality_code&192)===0)?"Último valor conhecido:":"Valor:";const fields=[["Item ID:",v.item_id],[valueLabel,formatValue(s)],[labSnapshot?"Timestamp de origem (snapshot):":"Timestamp de origem:",s?formatTimestamp(s.source_timestamp):"Sem leitura"],["Tipo:",v.data_type],[labSnapshot?"Tipo declarado no snapshot:":"VARTYPE de origem:",labSnapshot?(s?.type_evidence||"Schema do cache")+(s?.source_vartype?` · VARTYPE ${s.source_vartype}`:""):(s?.source_vartype??"—")],["Qualidade:",q.label],["Item ID de origem:",v.source_id||"Não configurada"]];if(labSnapshot)fields.push(["Comunicação:",({connected:"Conectado",degraded:"Degradado",unavailable:"Indisponível",invalid:"Configuração inválida"}[s?.snapshot_status]||"Aguardando snapshot")]);else fields.push(["HRESULT:",s?.hresult==null?"—":`0x${(s.hresult>>>0).toString(16).padStart(8,"0")}`]);for(const [label,value]of fields){const field=textEl("div",label);field.append(textEl("span",value));grid.append(field);}const statusText=labSnapshot?(s?.snapshot_message||"Snapshot ainda não confirmado"):`${sampleOriginLabel(v,s)}${sourceMode?.read_method?` · ${sourceMode.read_method}`:""}`;const provenance=labSnapshot?"O valor vem do snapshot HTTP do cache simulado; não é leitura OPC DA.":labOpc?"Leitura COM OPC DA real de um servidor simulador separado no laboratório.":s?.source_kind==="opc_da"?"Amostra recebida por leitura OPC DA; a fonte foi identificada no estado da coleta.":ancestor("modules",v)?.source_kind==="opc_da"?"Origem OPC DA configurada; nenhuma amostra confirmada. Consulte o estado da coleta.":"Fonte simulada local; OPC UA remoto ainda não conectado.";panel.append(grid,textEl("div",`${sampleOriginLabel(v,s)} · ${statusText}${s?.snapshot_checked_at?` · Verificado: ${formatTimestamp(s.snapshot_checked_at)}`:""}${model.monitored.has(v.id)?" · consultas periódicas":""} · ${provenance}`,"details-foot"));}
function updateStatus(){
 $("status-count").textContent=`Variáveis: ${allVariables().length}`;
 $("status-path").textContent=folder()?`Pasta: ${ancestors(folder()).map(n=>n.name).join(" › ")}`:"Nenhuma pasta selecionada";
 const machine=ancestor("machines"),d=machine?.deployment;
 $("save-status").textContent=model.state?`Revisão ${model.state.revision} salva · Windows: ${deploymentLabel(d?.status)}`:"Carregando configuração…";
 const windows=[...model.samples.values()].some(s=>s.applied_to_windows===true);
 const snapshot=[...model.samples.values()].find(s=>s.lab_snapshot),labSnapshot=!!snapshot||[...model.nodes.values()].some(v=>v.kind==="variables"&&sourceReadMode(v)?.transport==="rust_lab_loopback_snapshot");
 const snapshotFailed=snapshot&&snapshot.snapshot_status!=="connected";
 $("connection-status").textContent=model.connected?(labSnapshot?`Snapshot de laboratório · Fonte simulada · ${snapshotFailed?"indisponível":"conectado"}`:windows?([...model.samples.values()].some(s=>s.source_kind==="opc_da")?"Cache Windows · OPC DA":"Cache Windows · fonte simulada"):"Prévia simulada Linux"):"Sem leitura atual";
 $("status-dot").className=`status-dot${model.connected&&(!snapshot||!snapshotFailed)?" connected":model.failed.size||snapshotFailed?" error":""}`;
 renderDeployment();
}
function updateActions(){for(const [action,kind]of [["new-machine","machines"],["new-module","modules"],["new-folder","folders"],["new-variable","variables"]]){const button=document.querySelector(`[data-action="${action}"]`);button.disabled=!ancestor(kindMeta[kind].parent);button.title=button.disabled?`Selecione ${kindMeta[kindMeta[kind].parent].label} na árvore.`:kindMeta[kind].title;}document.querySelector('[data-action="disconnect"]').disabled=!model.connected&&!model.monitored.size;updateStatus();}
function variableMenu(v,x,y){showMenu([{label:"Ler valor",icon:"eye",testid:"context-read",action:()=>readVariable(v.id)},{label:model.monitored.has(v.id)?"Parar monitoramento":"Monitorar",icon:"monitor",testid:"context-monitor",action:()=>toggleMonitor(v.id)},{label:"Editar",icon:"edit",testid:"context-edit",action:()=>openEditor("variables",v)},{separator:true},{label:"Excluir",icon:"trash",testid:"context-delete",danger:true,action:()=>confirmDelete(v)}],x,y);}
function closeMenu(){$("context-menu").hidden=true;document.querySelectorAll("[data-menu]").forEach(b=>b.setAttribute("aria-expanded","false"));}
function showMenu(items,x,y){const menu=$("context-menu");menu.replaceChildren();for(const item of items){if(item.separator){menu.append(textEl("div",undefined,"menu-divider"));continue;}const button=textEl("button");button.type="button";button.setAttribute("role","menuitem");button.disabled=!!item.disabled;if(item.testid)button.dataset.testid=item.testid;if(item.danger)button.classList.add("danger");button.append(icon(item.icon||"tag"),textEl("span",item.label));button.addEventListener("click",()=>{closeMenu();item.action();});menu.append(button);}menu.hidden=false;const rect=menu.getBoundingClientRect();menu.style.left=`${Math.max(5,Math.min(x,innerWidth-rect.width-6))}px`;menu.style.top=`${Math.max(5,Math.min(y,innerHeight-rect.height-6))}px`;}
function showTopMenu(name,button){const n=current(),rect=button.getBoundingClientRect();let items=[];const edit=[{label:"Editar seleção",icon:"edit",testid:"menu-edit-selection",disabled:!n,action:()=>openEditor(n.kind,n)},{label:"Excluir seleção",icon:"trash",testid:"menu-delete",danger:true,disabled:!n,action:()=>confirmDelete(n)}];if(name==="file")items=[{label:"Nova conexão",icon:"connection",action:()=>openEditor("connections")},{label:"Recarregar configuração",icon:"refresh",action:()=>loadState()}];else if(name==="edit")items=edit;else if(name==="view")items=[{label:"Atualizar configuração e leituras",icon:"refresh",action:refreshAll},{label:"Detalhes da variável",icon:"tag",disabled:!model.variable,action:()=>{$("variable-details").hidden=!$("variable-details").hidden;}}];else if(name==="communication")items=[{label:"Consultar valores",icon:"play",action:connectSimulator},{label:"Parar consultas",icon:"stop",action:disconnectSimulator},{separator:true},{label:"Simular perda da fonte Linux",icon:"monitor",disabled:!!ancestor("machines")?.deployment?.configured,action:()=>setAvailability(false)},{label:"Simular retorno da fonte Linux",icon:"refresh",disabled:!!ancestor("machines")?.deployment?.configured,action:()=>setAvailability(true)}];else items=[{label:name==="tools"?"Arquitetura e limites":"Sobre este configurador",icon:"info",action:showAbout}];showMenu(items,rect.left,rect.bottom+2);button.setAttribute("aria-expanded","true");}
function makeField(name,label,value="",options={}){const wrap=textEl("div",undefined,"field");const labelEl=textEl("label",label);labelEl.htmlFor=`field-${name}`;let input;if(options.choices){input=document.createElement("select");for(const [val,text]of options.choices){const option=textEl("option",text);option.value=val;input.append(option);}input.value=String(value);}else if(options.multiline){input=document.createElement("textarea");input.value=value??"";}else{input=document.createElement("input");input.type="text";input.value=value??"";input.autocomplete="off";if(options.inputMode)input.inputMode=options.inputMode;}input.id=`field-${name}`;input.name=name;input.dataset.testid=`field-${name}`;input.required=!!options.required;input.maxLength=options.maxLength||2048;if(options.placeholder)input.placeholder=options.placeholder;wrap.append(labelEl,input);if(options.help)wrap.append(textEl("small",options.help));return wrap;}
function opcDaSourceFields(existing) {
  const kind=makeField("source_kind","Tipo de origem",existing?.source_kind||"simulated",{choices:[["simulated","Simulada — laboratório"],["opc_da","OPC DA — agente Windows autorizado"]]});
  const panel=textEl("div"),host=makeField("source_host","IP / hostname da máquina de origem",existing?.source_host||"",{maxLength:253});
  const search=textEl("button","Buscar servidores");search.type="button";search.dataset.testid="discover-opc-da";
  const message=textEl("p","Busca local no Windows via agente provisionado. Registro não comprova disponibilidade.","future-note");message.dataset.testid="source-discovery-status";
  const select=makeField("source_progid","Servidor OPC DA",existing?.source_progid||"",{choices:[]});
  const input=select.querySelector("select");let discovery=existing?.source_discovery_id||"",clsid=existing?.source_clsid||"",candidates=[];
  function reset(){input.replaceChildren();const option=textEl("option","Busque e selecione um servidor");option.value="";input.append(option);discovery="";clsid="";candidates=[];}
  reset();if(existing?.source_progid){const option=textEl("option",existing.source_progid);option.value=existing.source_progid;input.append(option);input.value=existing.source_progid;discovery=existing.source_discovery_id;clsid=existing.source_clsid;}
  host.querySelector("input").addEventListener("input",()=>{reset();message.textContent="Origem alterada; busque os servidores novamente.";});
  input.addEventListener("change",()=>{clsid=candidates.find(c=>c.prog_id===input.value)?.clsid||clsid;});
  search.addEventListener("click",async()=>{const requested=host.querySelector("input").value.trim();search.disabled=true;reset();message.textContent="Buscando no agente da origem…";try{const data=await api("/opc-da/discover",{method:"POST",body:{host:requested}});if(!panel.isConnected||host.querySelector("input").value.trim()!==requested)return;candidates=data.servers;discovery=data.discovery_id;for(const c of candidates){const option=textEl("option",`${c.prog_id} (${c.registry_view||data.identity.registry_view})`);option.value=c.prog_id;input.append(option);}message.textContent=`Agente confirmado: ${data.identity.hostname} — ${requested}. ${candidates.length} servidor(es) candidato(s). ${candidates.length?"Selecione explicitamente um servidor.":"Nenhum servidor encontrado nesta view do Registro."}`;}catch(error){message.textContent=error.message;}finally{search.disabled=false;}});
  const interval=makeField("update_interval_s","Intervalo entre leituras (segundos)",existing?.update_interval_s||10,{inputMode:"numeric",help:"Mínimo 5 segundos. Padrão 10 s; timeout COM 10 s. Somente os itens cadastrados serão lidos."});
  panel.append(host,search,message,select,interval);
  const toggle=()=>{panel.hidden=kind.querySelector("select").value!=="opc_da";};kind.querySelector("select").addEventListener("change",toggle);toggle();
  return {elements:[kind,panel],values:()=>({source_discovery_id:discovery,source_clsid:clsid})};
}
function setInitialField(type,value){const old=$("initial-field");const next=makeField("initial_value","Valor inicial simulado *",value,{required:true});
  next.id="initial-field";next.querySelector("label").textContent="Valor inicial simulado *";
  if(type==="Boolean"){const select=document.createElement("select");select.id="field-initial_value";select.name="initial_value";select.dataset.testid="field-initial_value";for(const [val,label]of [["false","False (falso)"],["true","True (verdadeiro)"]]){const option=textEl("option",label);option.value=val;select.append(option);}select.value=String(value===true||value==="true");next.querySelector("input").replaceWith(select);}else{const input=next.querySelector("input");input.required=type!=="String";input.inputMode=type==="String"?"text":type==="Integer"?"numeric":"decimal";input.value=type==="String"?String(value??""):typeof value==="number"?String(value):"0";input.placeholder=type==="String"?"Texto":type==="Integer"?"Ex.: 42":"Ex.: 6,25";}
  if(old)old.replaceWith(next);return next;
}
function openModal(title,iconName,body,submit,label="Salvar",hint="Salvo no gerenciamento · aplicação no Windows pendente"){
  closeMenu();model.lastFocus=document.activeElement;model.modal={submit};$("dialog-title").textContent=title;$("dialog-icon").replaceChildren(icon(iconName).firstChild);$("dialog-body").replaceChildren(...body);$("form-error").hidden=true;$("dialog-save").textContent=label;$("dialog-save").disabled=false;$("dialog-cancel").hidden=false;$("dialog-hint").textContent=hint;const dialog=$("dialog");dialog.style.left="";dialog.style.top="";$("modal-layer").hidden=false;requestAnimationFrame(()=>{const r=dialog.getBoundingClientRect();dialog.style.left=`${Math.max(15,(innerWidth-r.width)/2)}px`;dialog.style.top=`${Math.max(15,(innerHeight-r.height)/2)}px`;const first=dialog.querySelector("input, select, textarea, button:not(.dialog-x)");if(!dialog.contains(document.activeElement))first?.focus();});
}
function closeModal(){if(model.busy)return;$("modal-layer").hidden=true;model.modal=null;model.lastFocus?.focus();}
function openEditor(kind,existing=null){const meta=kindMeta[kind];if(!meta)return;const parent=existing?node(existing.parent):meta.parent?ancestor(meta.parent):null;if(meta.parent&&!parent){notify(`Selecione ${kindMeta[meta.parent].label} antes de continuar.`);return;}
  const real=kind==="variables"&&ancestor("modules",parent)?.source_kind==="opc_da";
  let sourceEditor=null;const body=[];if(parent)body.push(textEl("p",ancestors(parent).map(n=>n.name).join(" › "),"form-context"));
  body.push(makeField("name",`${kind==="variables"?"Nome da variável":"Nome"} *`,existing?.name||"",{required:true,maxLength:80,placeholder:kind==="variables"?"Ex.: Pressao":kind==="machines"?"Ex.: Máquina 2":kind==="modules"?"Ex.: Módulo 1":kind==="folders"?"Ex.: Pasta 1":"Ex.: Laboratório"}));
  if(kind==="connections")body.push(makeField("description","Descrição",existing?.description||"",{multiline:true,maxLength:2000}));
  if(kind==="machines"){body.push(makeField("host","Endereço / IP da máquina de destino *",existing?.host||"",{required:true,maxLength:253,placeholder:"destino.exemplo.local",help:"Endereço do destino. Salvar não aplica; o canal SSH exige provisionamento e confirmação de identidade."}));body.push(textEl("p","Após salvar, use Verificar agente e Aplicar na máquina. Origens podem ser simuladas ou OPC DA via agente autorizado; OPC UA continua futuro.","future-note"));}
  if(kind==="modules"){sourceEditor=opcDaSourceFields(existing);body.push(...sourceEditor.elements);}
  if(kind==="variables"){
    const type=existing?.data_type||"Double";const row=textEl("div",undefined,"form-row");row.append(makeField("data_type","Tipo *",type,{choices:[["Double","Real — Double (VT_R8)"],["Integer","Inteiro — 32 bits (VT_I4)"],["String","String — texto (VT_BSTR)"],["Boolean","Booleano — verdadeiro/falso (VT_BOOL)"]]}));const initial=setInitialField(type,existing?.initial_value??0);row.append(initial);body.push(row);
    body.push(makeField("item_id","Item ID OPC DA publicado",existing?.item_id||"",{maxLength:512,placeholder:"Automático: Máquina.Módulo.Pasta.Variável",help:"Único na máquina de destino. Após salvar, permanece estável ao renomear os níveis da árvore."}));
    body.push(makeField("source_id","Item ID de origem",existing?.source_id||"",{maxLength:1024,placeholder:"Ex.: Simulado.Pressao",help:"Exato no servidor OPC DA selecionado. É independente do Item ID publicado."}));
    if(real){body.push(makeField("source_conversion","Conversão da origem",existing?(existing.source_conversion||""):"automatic",{choices:[["automatic","Automática — detectar tipo retornado pelo servidor OPC DA"],["","Sem conversão — tipos devem coincidir"],["float_to_double","Float → Real (compatibilidade anterior)"]],help:"O agente valida o valor recebido e converte para o tipo de saída escolhido. Frações para Inteiro, overflow e valores incompatíveis são recusados. Qualidade e timestamp de origem são preservados."}));}
    body.push(makeField("description","Descrição",existing?.description||"",{multiline:true,maxLength:2000}));const readonly=textEl("p",undefined,"readonly-note");readonly.append(icon("shield"),textEl("span","Somente leitura. No modo OPC DA, o valor inicial simulado não é usado como amostra real."));body.push(readonly);
  }
  openModal(existing?`Editar ${meta.label}`:meta.title,meta.icon,body,async()=>{
    const form=$("editor-form"),data=Object.fromEntries(new FormData(form));
    data.name=data.name.trim();if(!data.name)throw new Error("Informe o nome.");
    if(data.host!==undefined){data.host=data.host.trim();if(!data.host)throw new Error("Informe o endereço ou IP da máquina de destino.");}
    if(kind==="modules"){
      data.source_endpoint=existing?.source_endpoint||"";
      if(data.source_kind==="opc_da"){Object.assign(data,sourceEditor.values());data.update_interval_s=Number(data.update_interval_s);if(!data.source_progid||!data.source_discovery_id||!data.source_clsid)throw new Error("Busque e selecione o servidor OPC DA.");}
      else for(const key of ["source_host","source_progid","source_clsid","source_discovery_id","update_interval_s"])delete data[key];
    }
    if(kind==="variables"){
      if(real){data.initial_value=existing?.data_type===data.data_type?existing.initial_value:({Boolean:false,Double:0,Integer:0,String:""}[data.data_type]);}
      else if(data.data_type==="Boolean")data.initial_value=data.initial_value==="true";
      else if(data.data_type!=="String"){
        const raw=data.initial_value.trim();if(!raw)throw new Error("Informe o valor inicial simulado.");
        data.initial_value=Number(raw.replace(",","."));
        if(!Number.isFinite(data.initial_value))throw new Error("Informe um número finito, por exemplo 6,25.");
        if(data.data_type==="Integer"&&(!Number.isInteger(data.initial_value)||data.initial_value< -2147483648||data.initial_value>2147483647))throw new Error("Inteiro deve estar entre -2147483648 e 2147483647, sem fração.");
      }
      if(data.data_type==="String"&&data.initial_value.length>4096)throw new Error("String deve ter até 4096 unidades UTF-16.");
      if(!data.item_id.trim())delete data.item_id;if(!data.source_id.trim())delete data.source_id;
      if(real&&data.source_conversion==="automatic")data.source_data_type="Auto";
      else if(real&&data.source_conversion==="float_to_double"){
        if(data.data_type!=="Double")throw new Error("Float → Real exige saída Real/Double.");data.source_data_type="Float";
      }else{data.source_data_type=null;data.source_conversion=null;}
    }
    if(!existing&&parent)data.parent_id=parent.id;
    await mutate(kind,existing?.id,data,existing?"PATCH":"POST");
  });
  if(kind==="variables"){
    if(real){$("initial-field").hidden=true;$("field-initial_value").required=false;$("field-source_id").required=true;}
    $("field-data_type").addEventListener("change",()=>{setInitialField($("field-data_type").value,({Boolean:false,Double:0,Integer:0,String:""}[$("field-data_type").value]));if(real){$("initial-field").hidden=true;$("field-initial_value").required=false;if($("field-data_type").value!=="Double"&&$("field-source_conversion").value==="float_to_double")$("field-source_conversion").value="automatic";}});
  }
}
async function mutate(kind,id,data,method){const oldIds=new Set(model.nodes.keys());let result;try{result=await api(`/${kind}${id?`/${encodeURIComponent(id)}`:""}`,{method,headers:{"If-Match":String(model.state.revision)},body:method==="DELETE"?undefined:data});}catch(error){if(error.payload?.error?.code==="revision_conflict"||error.status===428){await loadState(false);throw new Error(`${error.message} A configuração foi recarregada; revise os dados e tente salvar novamente.`);}throw error;}
  model.samples.clear();model.failed.clear();applyState(result.state);if(method==="POST"){const created=[...model.nodes.values()].find(n=>n.kind===kind&&!oldIds.has(n.id));if(created){if(kind==="variables"){model.selected=created.parent;model.variable=created.id;}else{model.selected=created.id;model.variable=null;}}}else if(method==="PATCH"&&kind==="variables")model.variable=id;renderTree();renderContent();updateActions();clearNotice();await pollEvents();
}
function descendantsCount(n){let count=0;const child=kindMeta[n.kind].child;if(!child)return 0;for(const entry of n[child]||[]){count+=1;count+=descendantsCount(node(entry.id));}return count;}
function confirmDelete(n){const body=[textEl("p",`Excluir ${kindMeta[n.kind].label} “${n.name}”?`,"dialog-copy")];const count=descendantsCount(n);if(count)body.push(textEl("p",`Esta exclusão também removerá ${count} ${count===1?"registro subordinado":"registros subordinados"} da configuração local.` ,"delete-warning"));body.push(textEl("p","A operação altera somente o gerenciamento no Linux. Nenhuma configuração de um agente Windows real será modificada.","dialog-copy"));openModal("Confirmar exclusão","trash",body,()=>mutate(n.kind,n.id,undefined,"DELETE"),"Excluir");}
function showAbout(){const body=[textEl("p","OPC Bridge — Configurador de laboratório","dialog-copy"),textEl("p","A interface, o backend persistente e o agente simulado estão separados. As leituras indicam fonte simulada ou OPC DA real; agentes de origem precisam de provisionamento e identidade confirmada.","dialog-copy"),textEl("p","Máquinas provisionadas recebem configurações pelo agente SSH autenticado. O estado aplicado exige confirmação do Windows. A origem OPC UA remota continua futura.","future-note"),textEl("p","Fechar o navegador ou perder o Linux não remove a última configuração válida salva no agente Windows.","dialog-copy")];openModal("Arquitetura e limites","info",body,async()=>{},"Entendido", "Gerenciamento de laboratório · somente leitura");$("dialog-cancel").hidden=true;}
function acceptSamples(data){if(typeof data.simulated!=="boolean")throw new Error("Resposta sem identificação explícita da origem.");const samples=data.samples||(data.sample?[data.sample]:[]);for(const sample of samples){if(node(sample.variable_id)){model.samples.set(sample.variable_id,sample);model.failed.delete(sample.variable_id);}}model.connected=true;renderTable();renderDetails();updateActions();}
function markReadFailure(ids,error){const invalid=[...new Set([...model.samples.keys(),...ids])];model.samples.clear();for(const id of invalid){model.failed.add(id);}model.connected=false;renderTable();renderDetails();updateActions();notify(error.message);}
async function readVariable(id){const generation=model.readGeneration;try{const data=await api(`/variables/${encodeURIComponent(id)}/read`);if(generation!==model.readGeneration)return;acceptSamples(data);clearNotice();await pollEvents();}catch(error){if(generation===model.readGeneration)markReadFailure([id],error);}}
async function readSamples(ids,quiet=false){if(!ids.length)return;const generation=model.readGeneration;try{const data=await api(`/samples?ids=${encodeURIComponent(ids.join(","))}`);if(generation!==model.readGeneration)return;acceptSamples(data);if(!quiet)clearNotice();}catch(error){if(generation===model.readGeneration)markReadFailure(ids,error);}}
async function connectSimulator(){const generation=model.readGeneration;try{await api("/health");if(generation!==model.readGeneration)return;model.connected=true;updateActions();const ids=(folder()?.variables||[]).map(v=>v.id);if(ids.length)await readSamples(ids);clearNotice();}catch(error){markReadFailure([],error);}}
function disconnectSimulator(){model.readGeneration++;model.connected=false;model.monitored.clear();model.samples.clear();model.failed.clear();renderTable();renderDetails();updateActions();clearNotice();}
async function refreshAll(){if(await loadState()){const ids=(folder()?.variables||[]).map(v=>v.id);if(ids.length)await readSamples(ids);await pollEvents();}}
function toggleMonitor(id){if(model.monitored.has(id))model.monitored.delete(id);else{model.monitored.add(id);readSamples([id]);}renderTable();renderDetails();updateActions();}
async function setAvailability(available){try{await api("/simulator/availability",{method:"POST",body:{available}});const ids=[...new Set([...model.monitored,...(folder()?.variables||[]).map(v=>v.id)])];await readSamples(ids);await pollEvents();notify(available?"Fonte simulada disponível. As próximas amostras são geradas pelo simulador.":"Perda da fonte simulada ativada. Os valores retidos são apresentados com qualidade ruim.");}catch(error){notify(error.message);}}
const eventLabels={info:"Informação",success:"Sucesso",warning:"Aviso",error:"Erro"};
async function pollEvents(){try{const data=await api(`/events?after=${model.eventCursor}&limit=100`);const known=new Set(model.events.map(e=>e.id));for(const event of data.events||[]){if(!known.has(event.id))model.events.push(event);model.eventCursor=Math.max(model.eventCursor,Number(event.id)||0);}model.events=model.events.sort((a,b)=>a.id-b.id).slice(-250);renderEvents();}catch(error){if(model.connected)markReadFailure([...model.samples.keys()],error);}}
function renderEvents(){const body=$("events-body"),filter=$("event-filter").value;body.replaceChildren();const events=model.events.filter(e=>filter==="all"||e.level===filter).slice().reverse();for(const event of events){const row=document.createElement("tr");const indicator=textEl("td"),badge=textEl("span",event.level==="success"?"✓":event.level==="warning"?"!":event.level==="error"?"!":"i",`event-icon ${event.level}`);indicator.append(badge);row.append(indicator,textEl("td",formatTimestamp(event.time)),textEl("td",eventLabels[event.level]||event.level),textEl("td",event.origin||"Gerenciamento"),textEl("td",event.message,"event-message"));for(const cell of row.cells)cell.title=cell.textContent;body.append(row);}$("event-count").textContent=String(model.events.length);$("no-events").hidden=events.length>0;}
const actions={"new-connection":()=>openEditor("connections"),"new-machine":()=>openEditor("machines"),"new-module":()=>openEditor("modules"),"new-folder":()=>openEditor("folders"),"new-variable":()=>openEditor("variables"),connect:connectSimulator,disconnect:disconnectSimulator,refresh:refreshAll};
document.querySelectorAll("[data-action]").forEach(button=>button.addEventListener("click",()=>actions[button.dataset.action]?.()));
document.querySelectorAll("[data-menu]").forEach(button=>button.addEventListener("click",e=>{e.stopPropagation();const open=button.getAttribute("aria-expanded")==="true";closeMenu();if(!open)showTopMenu(button.dataset.menu,button);}));
$("notice-close").addEventListener("click",clearNotice);$("dialog-x").addEventListener("click",closeModal);$("dialog-cancel").addEventListener("click",closeModal);$("event-filter").addEventListener("change",renderEvents);
$("editor-form").addEventListener("submit",async e=>{e.preventDefault();if(model.busy||!model.modal)return;model.busy=true;$("dialog-save").disabled=true;$("form-error").hidden=true;try{await model.modal.submit();model.busy=false;closeModal();}catch(error){$("form-error").textContent=error.message;$("form-error").hidden=false;model.busy=false;$("dialog-save").disabled=false;}});
document.addEventListener("pointerdown",e=>{if(!$("context-menu").contains(e.target))closeMenu();});
document.addEventListener("keydown",e=>{if(e.key==="Escape"){closeMenu();if(!$("modal-layer").hidden){e.preventDefault();closeModal();}}if(!$("modal-layer").hidden&&e.key==="Tab"){const inputs=[...$("dialog").querySelectorAll("button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled)")].filter(el=>el.getClientRects().length);const first=inputs[0],last=inputs[inputs.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
let drag=null;$("dialog-handle").addEventListener("pointerdown",e=>{if(e.target.closest("button"))return;const r=$("dialog").getBoundingClientRect();drag={x:e.clientX-r.left,y:e.clientY-r.top};$("dialog-handle").setPointerCapture(e.pointerId);});$("dialog-handle").addEventListener("pointermove",e=>{if(!drag)return;const dialog=$("dialog"),r=dialog.getBoundingClientRect();dialog.style.left=`${Math.max(10,Math.min(innerWidth-r.width-10,e.clientX-drag.x))}px`;dialog.style.top=`${Math.max(10,Math.min(innerHeight-r.height-10,e.clientY-drag.y))}px`;});$("dialog-handle").addEventListener("pointerup",()=>{drag=null;});$("dialog-handle").addEventListener("pointercancel",()=>{drag=null;});
window.addEventListener("resize",()=>{closeMenu();if(!$("modal-layer").hidden){const r=$("dialog").getBoundingClientRect();$("dialog").style.left=`${Math.max(10,Math.min(r.left,innerWidth-r.width-10))}px`;$("dialog").style.top=`${Math.max(10,Math.min(r.top,innerHeight-r.height-10))}px`;}});

function deploymentLabel(status){return {applied:"Aplicada",pending:"Pendente",applying:"Aplicando…",failed:"Falhou",indeterminate:"Indeterminada",unconfigured:"Sem agente provisionado"}[status]||"Pendente";}
function renderDeployment(){
 const m=ancestor("machines"),panel=$("deployment-panel");panel.hidden=!m;if(!m)return;
 const d=m.deployment||{status:"unconfigured",configured:false};
 $("deployment-machine").textContent=`${m.name} — ${m.host}`;
 const actual=d.identity_hostname||d.expected_hostname;
 $("deployment-status").textContent=`${deploymentLabel(d.status)}${actual?` · Identidade Windows: ${actual}`:""}${d.confirmed_at?` · Confirmada: ${formatTimestamp(d.confirmed_at)}`:""}${d.receipt?` · Última confirmação: ${d.receipt.variable_count} variáveis · SHA ${d.receipt.project_sha256.slice(0,12)} · Leitura requer cliente OPC DA conectado`:""}`;
 $("deployment-error").textContent=d.last_error||(actual&&actual.toLowerCase()!==m.name.toLowerCase()?`O nome cadastrado ${m.name} foi preservado; o Windows identificado é ${actual}.`:!d.configured?"Provisionamento do canal SSH necessário antes de aplicar.":"");
 $("verify-agent").disabled=!d.configured||d.status==="applying";
 $("apply-machine").disabled=!d.configured||d.status==="applying";
 $("deployment-pill").textContent=deploymentLabel(d.status);
 if(m.modules?.some(mod=>mod.source_kind==="opc_da")) $("deployment-status").textContent+=` · Coleta: ${m.sourceState?.message||"sem confirmação de amostras"}`;
 $("scope-text").textContent=d.status==="applied"?"Configuração confirmada no Windows. Valores podem atualizar sem reinício; mudanças estruturais exigem parar/reconectar o laboratório.":"Configuração salva no Linux. A aplicação no Windows exige confirmação do agente; a origem OPC UA continua futura.";
}
async function deploymentOperation(operation){const m=ancestor("machines");if(!m)return;
 try{const result=await api(`/machines/${m.id}/${operation}`,{method:"POST",headers:{"If-Match":String(model.state.revision)},body:{}});m.deployment=result.deployment;renderDeployment();await pollEvents();}catch(error){notify(error.message);}
}
function confirmApply(){const m=ancestor("machines");if(!m)return;
 const body=[textEl("p",`Enviar somente a configuração de ${m.name} (${m.host})?`,"dialog-copy"),textEl("p",`Identidade provisionada: ${m.deployment?.expected_hostname||"não configurada"}. Os nomes e Item IDs cadastrados serão preservados.`,"dialog-copy"),textEl("p","Mudanças somente de valores preservam o servidor e os clientes. Mudanças estruturais serão recusadas com servidor ativo; exigem encerramento/reconexão. Origens OPC DA devem ter agente autorizado. OPC UA não será conectado.","delete-warning")];
 openModal("Aplicar na máquina de destino","machine",body,()=>deploymentOperation("apply"),"Aplicar");
}
$("verify-agent").addEventListener("click",()=>deploymentOperation("probe"));
$("apply-machine").addEventListener("click",confirmApply);
let checkingDeployment=false;
setInterval(async()=>{const m=ancestor("machines");if(!m?.deployment?.configured||checkingDeployment)return;checkingDeployment=true;try{const result=await api(`/machines/${m.id}/deployment`);const old=m.deployment.status;m.deployment=result.deployment;if(m.modules?.some(mod=>mod.source_kind==="opc_da"))m.sourceState=await api(`/machines/${m.id}/source-status`);if(old!==result.deployment.status){model.readGeneration++;model.samples.clear();renderTable();renderDetails();}updateStatus();}catch(_){}finally{checkingDeployment=false;}},2000);

hydrateIcons();updateActions();loadState().then(async loaded=>{if(loaded){await startPersistentLabSnapshot();await pollEvents();}});
setInterval(async()=>{if(model.polling)return;model.polling=true;try{if(model.monitored.size)await readSamples([...model.monitored],true);await pollEvents();}finally{model.polling=false;}},1000);
