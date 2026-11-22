'use strict';
const $ = id => document.getElementById(id);
const stateNames = {pending:'Pendiente',running:'Procesando',passed:'Procesado',failed:'Falló',blocked:'Bloqueado',unverified:'Sin verificar'};
let report=null, labels={}, selected=null, geometry=null, generation=0, previewKey='', yaw=-.5, pitch=-.28, zoom=1, follow=true;
const canvas=$('geometry'), ctx=canvas.getContext('2d');
function node(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function assetURL(rel){return '/api/artifacts/'+rel.split('/').map(encodeURIComponent).join('/');}
function apply(data){
  labels=data.labels||labels;
  if(!data.report){report=null;geometry=null;$('reason').textContent=data.error||'Esperando un reporte en esta carpeta.';$('stages').replaceChildren();draw();return;}
  const changed=report?.run_id!==data.report.run_id;
  report=data.report;
  if(changed){generation++;geometry=null;previewKey='';selected=null;follow=true;draw();}
  $('session').textContent=report.session_id;
  $('run').textContent='Ejecución '+report.run_id;
  $('evidence').textContent={synthetic:'Ensayo sintético',recorded:'Capturas grabadas',live:'Captura en vivo'}[report.evidence];
  $('physical').textContent=report.physical_verified?'Precisión física verificada en el alcance declarado':'Precisión física sin verificar';
  if(follow)selected=Object.keys(report.stages).find(k=>report.stages[k].state==='running')||Object.keys(report.stages).find(k=>['failed','blocked'].includes(report.stages[k].state))||'register_render';
  selected=selected||'preflight';
  $('stages').replaceChildren();
  for(const [name,s] of Object.entries(report.stages)){
    const li=node('li',undefined,'stage');li.dataset.state=s.state;
    const b=node('button');b.type='button';b.className=selected===name?'selected':'';b.setAttribute('aria-current',selected===name?'step':'false');
    b.append(node('span',labels[name]||name),node('small',stateNames[s.state]+(s.cached?' · reutilizado':'')));
    if(s.state==='running'){const p=node('progress');p.max=1;p.value=s.progress;b.append(p);}
    b.onclick=()=>{selected=name;follow=false;apply({report,labels});};li.append(b);$('stages').append(li);
  }
  detail();
  const key=JSON.stringify(Object.values(report.stages).map(s=>[s.state,s.outputs]));
  if(key!==previewKey){previewKey=key;const token=++generation;fetch('/api/preview').then(r=>r.ok?r.json():Promise.reject()).then(g=>{
    if(token===generation&&g.run_id===report?.run_id){geometry=g;draw();entities();}
  }).catch(()=>{if(token===generation){geometry=null;draw();}});}
}
function detail(){
  const s=report.stages[selected];$('stageTitle').textContent=labels[selected]||selected;$('stageState').textContent=stateNames[s.state];
  $('reason').textContent=s.reason|| (s.state==='passed'?'Artefactos completos. Revisa su evidencia y cobertura.':'La etapa espera sus entradas.');
  $('metrics').replaceChildren();
  const metrics={...s.counts,...s.metrics,'Tiempo (s)':s.duration_s};
  for(const [key,val] of Object.entries(metrics)){if(typeof val==='object')continue;const box=node('div',undefined,'metric');
    box.append(node('span',key.replaceAll('_',' ')),node('strong',typeof val==='number'?Number(val.toPrecision(5)).toLocaleString('es-CL'):String(val)));$('metrics').append(box);}
  $('images').replaceChildren();$('artifacts').replaceChildren();
  for(const rel of Object.keys(s.outputs)){const li=node('li'),a=node('a',rel.split('/').pop());a.href=assetURL(rel);a.target='_blank';a.rel='noopener';li.append(a);$('artifacts').append(li);
    if(/\.png$/i.test(rel)){const f=node('figure'),im=node('img');im.src=assetURL(rel);im.alt='Previsualización de '+rel.split('/').pop();f.append(im,node('figcaption',im.alt));$('images').append(f);}}
  $('details').textContent=JSON.stringify({evidence:s.evidence,units:s.units,inputs:s.inputs,outputs:s.outputs,fingerprint:s.fingerprint},null,2);
}
function entities(){
  $('entities').replaceChildren();if(!geometry)return;
  for(const [title,list] of [['Objetos',geometry.objects],['Luces',geometry.lights],['Materiales',geometry.materials]]){
    const section=node('section');section.append(node('h3',title));
    if(!list.length)section.append(node('p','Sin resultados publicados.'));
    for(const item of list){section.append(node('p',(item.label||item.id)+' · '+(item.observation||'')+(item.reason?'. '+item.reason:'')));}
    $('entities').append(section);
  }
}
function draw(){
  const rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;
  ctx.setTransform(dpr,0,0,dpr,0,0);const w=rect.width,h=rect.height;ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='#cfdee9';ctx.lineWidth=1;for(let x=0;x<w;x+=40){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke();}for(let y=0;y<h;y+=40){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  if(!geometry){$('viewHint').textContent='La geometría aparecerá al completar la triangulación.';return;}
  const pts=geometry.points||[],devices=$('showDevices').checked?geometry.devices:[];
  const all=[...pts,...devices.flatMap(d=>[d.center,...d.frustum])];if(!all.length){$('viewHint').textContent='Aún no hay geometría métrica disponible.';return;}
  $('viewHint').textContent=(report?.evidence==='synthetic'?'Geometría sintética. ':'')+'mm · arrastra para rotar; rueda para acercar'+(geometry.sampled?' · vista reducida':'');
  const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const p of all)for(let i=0;i<3;i++){lo[i]=Math.min(lo[i],p[i]);hi[i]=Math.max(hi[i],p[i]);}
  const center=lo.map((v,i)=>(v+hi[i])/2);
  function rotate(p){let [x,y,z]=p.map((v,i)=>v-center[i]);let xx=x*Math.cos(yaw)+z*Math.sin(yaw),zz=-x*Math.sin(yaw)+z*Math.cos(yaw);return [xx,y*Math.cos(pitch)-zz*Math.sin(pitch)];}
  const bounds=[Infinity,Infinity,-Infinity,-Infinity];for(const p of all){const [x,y]=rotate(p);bounds[0]=Math.min(bounds[0],x);bounds[1]=Math.min(bounds[1],y);bounds[2]=Math.max(bounds[2],x);bounds[3]=Math.max(bounds[3],y);}
  const scale=Math.min((w-90)/Math.max(1,bounds[2]-bounds[0]),(h-90)/Math.max(1,bounds[3]-bounds[1]))*zoom;
  function project(p){const [x,y]=rotate(p);return [w/2+(x-(bounds[0]+bounds[2])/2)*scale,h/2+(y-(bounds[1]+bounds[3])/2)*scale];}
  function line(a,b,color){a=project(a);b=project(b);ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();}
  if($('showMesh').checked)for(const f of geometry.faces){if(!$('showFill').checked&&f.some(i=>!geometry.measured[i]))continue;const p=f.map(i=>project(pts[i]));ctx.beginPath();ctx.moveTo(...p[0]);ctx.lineTo(...p[1]);ctx.lineTo(...p[2]);ctx.closePath();ctx.fillStyle='#3676a01a';ctx.fill();ctx.strokeStyle='#3676a065';ctx.stroke();}
  pts.forEach((p,i)=>{if(!geometry.measured[i]&&!$('showFill').checked)return;const [x,y]=project(p);ctx.fillStyle=geometry.measured[i]?'#285d83':'#b46b24';ctx.fillRect(x-1.5,y-1.5,3,3);});
  for(const d of devices){const col=d.kind==='projector'?'#98591e':'#3b7062';d.frustum.forEach((p,i)=>{line(d.center,p,col);line(p,d.frustum[(i+1)%4],col);});const [x,y]=project(d.center);ctx.fillStyle=col;ctx.font='13px sans-serif';ctx.fillText(d.name,x+6,y-7);}
}
let drag=null;canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);};canvas.onpointerup=()=>drag=null;canvas.onpointercancel=()=>drag=null;
canvas.onpointermove=e=>{if(!drag)return;yaw+=(e.clientX-drag[0])*.008;pitch+=(e.clientY-drag[1])*.008;drag=[e.clientX,e.clientY];draw();};
canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.2,Math.min(8,zoom*Math.exp(-e.deltaY*.001)));draw();},{passive:false});
canvas.onkeydown=e=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();yaw+=(e.key==='ArrowLeft'?-.1:e.key==='ArrowRight'?.1:0);pitch+=(e.key==='ArrowUp'?-.1:e.key==='ArrowDown'?.1:0);draw();}};
['showMesh','showFill','showDevices'].forEach(id=>$(id).onchange=draw);$('reset').onclick=()=>{yaw=-.5;pitch=-.28;zoom=1;draw();};new ResizeObserver(draw).observe(canvas);
const es=new EventSource('/api/events');es.onopen=()=>{$('connection').textContent='Conectado';$('connection').className='online';};
es.addEventListener('pipeline',e=>{try{apply(JSON.parse(e.data));}catch(err){$('connection').textContent='Error al leer el avance';console.error(err);}});
es.onerror=()=>{$('connection').textContent='Reconectando…';$('connection').className='';};
