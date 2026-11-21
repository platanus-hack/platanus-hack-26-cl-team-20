import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';

const $=id=>document.getElementById(id);
const canvas=$('scene');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
const world=new THREE.Scene();world.background=new THREE.Color('#dfe9f1');
const orbit=new THREE.PerspectiveCamera(48,1,1,15000);
orbit.position.set(1350,850,1400);
const controls=new OrbitControls(orbit,canvas);controls.target.set(0,-70,-920);controls.enableDamping=true;
const lens=new THREE.PerspectiveCamera();
const truthGroup=new THREE.Group(),estimated=new THREE.Group();world.add(truthGroup,estimated);
const cloudGroup=new THREE.Group(),meshGroup=new THREE.Group();world.add(cloudGroup,meshGroup);
let meshUrl=null,meshRequest=0;
const grid=new THREE.GridHelper(3000,30,0x9fb3c3,0xc3d2de);grid.position.set(0,-450,-900);world.add(grid);
const axes=new THREE.AxesHelper(130);world.add(axes);
const hemisphere=new THREE.HemisphereLight(0xffffff,0x81919d,2);world.add(hemisphere);
const light=new THREE.DirectionalLight(0xffffff,2);light.position.set(500,900,500);world.add(light);
let data=null,mode='orbit',selected=null,paused=false,lastGeometry='',lastTruth='',lastRun=null;
const patternTexture=new THREE.Texture();patternTexture.flipY=false;patternTexture.minFilter=THREE.LinearFilter;patternTexture.generateMipmaps=false;
export const projectionUniforms={pattern:{value:patternTexture},ready:{value:0},rotation:{value:new THREE.Matrix3()},translation:{value:new THREE.Vector3()},intrinsic:{value:new THREE.Vector4()},distortion:{value:new THREE.Vector4()},k3:{value:0},size:{value:new THREE.Vector2(1920,1080)}};
let patternRequest=0;
$('pattern-image').addEventListener('load',async()=>{
 const request=++patternRequest,bitmap=await createImageBitmap($('pattern-image'));
 if(request!==patternRequest){bitmap.close();return;}
 // The DOM image has CSS dimensions. Upload its intrinsic bitmap so GPU
 // storage and upload dimensions agree, including on responsive layouts.
 const texture=new THREE.Texture(bitmap);texture.flipY=false;texture.minFilter=THREE.LinearFilter;texture.generateMipmaps=false;texture.needsUpdate=true;
 const old=projectionUniforms.pattern.value;projectionUniforms.pattern.value=texture;projectionUniforms.ready.value=1;old.image?.close?.();old.dispose();
});
function projectedMaterial(color,opacity){return new THREE.ShaderMaterial({transparent:true,side:THREE.DoubleSide,depthWrite:opacity>.8,uniforms:{...projectionUniforms,albedo:{value:new THREE.Color(color)},alpha:{value:opacity}},vertexShader:`varying vec3 pointWorld; void main(){vec4 p=modelMatrix*vec4(position,1.0);pointWorld=p.xyz;gl_Position=projectionMatrix*viewMatrix*p;}`,fragmentShader:`
uniform sampler2D pattern; uniform float ready; uniform mat3 rotation; uniform vec3 translation; uniform vec4 intrinsic; uniform vec4 distortion; uniform float k3; uniform vec2 size; uniform vec3 albedo; uniform float alpha; varying vec3 pointWorld;
void main(){vec3 p=rotation*(pointWorld*vec3(1.0,-1.0,-1.0))+translation; vec2 n=p.xy/max(p.z,0.001); float r=dot(n,n);float radial=1.0+distortion.x*r+distortion.y*r*r+k3*r*r*r; vec2 d=n*radial+vec2(2.0*distortion.z*n.x*n.y+distortion.w*(r+2.0*n.x*n.x),distortion.z*(r+2.0*n.y*n.y)+2.0*distortion.w*n.x*n.y);vec2 uv=(d*intrinsic.xy+intrinsic.zw+0.5)/size;float inside=step(0.0,p.z)*step(0.0,uv.x)*step(0.0,uv.y)*step(uv.x,1.0)*step(uv.y,1.0);vec3 value=mix(vec3(0.55),texture2D(pattern,uv).rgb,ready)*inside;gl_FragColor=vec4(pow(albedo*(0.07+0.65*value),vec3(0.5)),alpha);}`});}
const vec=p=>new THREE.Vector3(p[0],-p[1],-p[2]);
const fixed=(n,d=3)=>Number.isFinite(n)?n.toFixed(d):'—';
function clear(group){while(group.children.length){const o=group.children[0];group.remove(o);o.traverse(x=>{x.geometry?.dispose();if(Array.isArray(x.material))x.material.forEach(m=>m.dispose());else x.material?.dispose();});}}
function lines(points,color,opacity=1){const g=new THREE.BufferGeometry().setFromPoints(points);return new THREE.LineSegments(g,new THREE.LineBasicMaterial({color,transparent:opacity<1,opacity}));}
function sheet(corners,color,opacity,group,projected=false){
 const p=corners.map(vec),g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(p.flatMap(v=>v.toArray()),3));g.setIndex([0,1,2,0,2,3]);g.computeVertexNormals();
 group.add(new THREE.Mesh(g,projected?projectedMaterial(color,opacity):new THREE.MeshStandardMaterial({color,transparent:true,opacity,side:THREE.DoubleSide,roughness:1,depthWrite:opacity>.8})));
 group.add(lines(p.flatMap((v,i)=>[v,p[(i+1)%4]]),color,Math.min(1,opacity+.2)));
}
function device(d,color,group,opacity=1){
 const c=vec(d.center_world),p=d.frustum_world.map(vec),points=[];
 p.forEach((v,i)=>points.push(c,v,v,p[(i+1)%4]));group.add(lines(points,color,opacity));
 const marker=new THREE.Mesh(new THREE.SphereGeometry(17,12,8),new THREE.MeshBasicMaterial({color,transparent:true,opacity}));marker.position.copy(c);group.add(marker);
}
function calibrateCamera(d){
 const [w,h]=d.size,K=d.K,n=1,f=15000;
 lens.projectionMatrix.makePerspective((-.5-K[0][2])*n/K[0][0],(w-.5-K[0][2])*n/K[0][0],(K[1][2]+.5)*n/K[1][1],(K[1][2]-h+.5)*n/K[1][1],n,f);
 lens.projectionMatrixInverse.copy(lens.projectionMatrix).invert();
 const R=d.R,t=d.t,B=[1,-1,-1];
 const m=new THREE.Matrix4().set(...[0,1,2].flatMap(i=>[...R[i].map((v,j)=>B[i]*v*B[j]),B[i]*t[i]]),0,0,0,1);
 lens.matrixAutoUpdate=false;lens.matrix.copy(m.invert());lens.matrix.decompose(lens.position,lens.quaternion,lens.scale);lens.updateMatrixWorld(true);
}
function cameraData(){
 if(data?.active_camera&&!selected&&data.devices?.[data.active_camera])return data.devices[data.active_camera];
 if(data?.mode==='scene'){const index=selected?Number(selected.split('_')[1]):Math.max(0,(data.pose||1)-1);const name=index?`osmo_${index}`:'osmo';if(data.devices?.[name])return data.devices[name];}
 return Object.values(data?.devices||{}).find(d=>d.kind==='camera')||Object.values(data?.truth_devices||{}).find(d=>d.kind==='camera');
}
function updateGeometry(){
 const key=JSON.stringify([data.devices,data.planes,data.cloud,selected]);
 if(key!==lastGeometry){lastGeometry=key;clear(estimated);clear(cloudGroup);
  for(const d of Object.values(data.devices||{}))device(d,d.kind==='camera'?'#3d8b99':'#bb784a',estimated);
  for(const p of data.planes||[])sheet(p.corners,p.name===selected?'#bd7a4b':'#6679ad',p.name===selected ? .35 : .09,estimated);
  if(data.cloud?.points){const g=new THREE.BufferGeometry().setFromPoints(data.cloud.points.map(vec));
   if(data.cloud.colors)g.setAttribute('color',new THREE.Float32BufferAttribute(data.cloud.colors.flat(),3));
   cloudGroup.add(new THREE.Points(g,new THREE.PointsMaterial({color:data.cloud.colors?'#ffffff':'#20c7d7',vertexColors:!!data.cloud.colors,size:data.approximate?3:6,sizeAttenuation:true})));}
 }
 if(data.mesh?.url!==meshUrl){meshUrl=data.mesh?.url;const request=++meshRequest;clear(meshGroup);
  if(meshUrl)fetch(meshUrl).then(r=>{if(!r.ok)throw new Error('No se pudo cargar malla');return r.json();}).then(m=>{
   if(request!==meshRequest)return;const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(m.vertices.flatMap(p=>vec(p).toArray()),3));
   g.setAttribute('uv',new THREE.Float32BufferAttribute(m.uv.flat(),2));g.setIndex(m.faces.flat());g.computeVertexNormals();
   const texture=new THREE.TextureLoader().load(m.texture);texture.flipY=false;texture.colorSpace=THREE.SRGBColorSpace;
   const material=new THREE.MeshBasicMaterial({map:texture,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:1,polygonOffsetUnits:1});meshGroup.add(new THREE.Mesh(g,material));window.meshLoaded={vertices:m.vertices.length,faces:m.faces.length};
  }).catch(e=>{$('message').textContent=e.message;});
 }
 cloudGroup.visible=$('show-cloud').checked;meshGroup.visible=$('show-mesh').checked;
 const truthScene=(data.views||[]).find(v=>v.name===selected)?.truth_scene||data.truth_scene;
 const tk=JSON.stringify([truthScene,data.truth_devices]);
 if(tk!==lastTruth){lastTruth=tk;clear(truthGroup);
  for(const s of truthScene||[])sheet(s.corners,new THREE.Color(...s.color),data.mode==='scene'?1:(s.name==='A4' ? .98 : .30),truthGroup,true);
  for(const d of Object.values(data.truth_devices||{}))device(d,'#8c9aad',truthGroup,.35);
 }
 truthGroup.visible=$('reference').checked&&data.evidence==='synthetic';
 grid.visible=!data.approximate;
 const pro=Object.values(data.truth_devices||{}).find(d=>d.kind==='projector');if(pro){projectionUniforms.rotation.value.set(...pro.R.flat());projectionUniforms.translation.value.fromArray(pro.t);projectionUniforms.intrinsic.value.set(pro.K[0][0],pro.K[1][1],pro.K[0][2],pro.K[1][2]);projectionUniforms.distortion.value.fromArray(pro.dist.slice(0,4));projectionUniforms.k3.value=pro.dist[4]||0;}
 const d=cameraData();if(d)calibrateCamera(d);
}
function setMode(next){mode=next;controls.enabled=mode==='orbit';$('orbit').classList.toggle('active',mode==='orbit');$('camera-view').classList.toggle('active',mode==='camera');$('view-note').textContent=mode==='camera'?'Vista 3D rectificada. La foto lateral incluye distorsión de lente.':'Arrastra para orbitar. Rueda para acercar.';}
$('orbit').onclick=()=>setMode('orbit');$('camera-view').onclick=()=>setMode('camera');
function centerView(){if(data?.initial_view){orbit.up.fromArray(data.initial_view.up);orbit.position.fromArray(data.initial_view.position);controls.target.fromArray(data.initial_view.target);}else{orbit.up.set(0,1,0);orbit.position.set(1350,850,1400);controls.target.set(0,-70,-920);}controls.update();setMode('orbit');}
$('reset-view').onclick=centerView;
$('show-cloud').onchange=()=>{cloudGroup.visible=$('show-cloud').checked;};$('show-mesh').onchange=()=>{meshGroup.visible=$('show-mesh').checked;};
$('reference').onchange=()=>updateGeometry();
function image(element,record,seq){if(!record)return;const img=$(element);const url=record.url+'?v='+seq;if(img.dataset.source!==url){img.src=url;img.dataset.source=url;}img.hidden=false;}
function updateImages(){
 const v=selected?(data.views||[]).find(v=>v.name===selected):null;
 image('camera-image',v?.camera||data.images?.camera,data.seq);image('pattern-image',v?.pattern||data.images?.pattern,data.seq);
 if(data.images?.camera){$('camera-empty').hidden=true;$('camera-size').textContent=data.images.camera.size.join(' × ');}
 $('follow').hidden=!selected;
}
$('follow').onclick=()=>{selected=null;update(data);};
function node(tag,text,className){const x=document.createElement(tag);x.textContent=text;if(className)x.className=className;return x;}
function update(state){
 data=state;
 if(lastRun!==data.run_id){selected=null;lastRun=data.run_id;lastGeometry='';lastTruth='';meshUrl=null;$('camera-image').hidden=true;$('pattern-image').hidden=true;$('camera-empty').hidden=false;if(data.initial_view)centerView();}
 const synthetic=data.evidence==='synthetic';$('evidence').textContent=synthetic?'Simulación con imágenes':'Captura real';
 const scene=data.mode==='scene';document.querySelector('.key.paper').textContent=data.approximate?'Nube + malla':scene?'Puntos 3D estimados':'Hojas reconstruidas';
 document.querySelector('.image-key').hidden=scene||data.approximate;
 document.querySelector('.image-key').style.display=scene||data.approximate?'none':'';
 $('scene-title').textContent=data.approximate?'Escena 3D · aproximada':data.status==='completed'?'Rig estimado':'Escena en construcción';
 $('accuracy').textContent=data.approximate?'Aproximado · sin validación de ≤2 mm':'Precisión física sin verificar';
 $('reference').parentElement.hidden=!synthetic;$('reference').parentElement.style.display=synthetic?'':'none';$('simulation-settings').hidden=!synthetic;
 $('stage').textContent=data.stage||'Esperando captura';$('pose-label').textContent=data.pose?`Pose ${data.pose} · ${selected||'seguimiento en vivo'}`:'Sin poses';
 $('count').textContent=`${data.pose||0} / ${data.total||0} poses`;$('progress').style.width=`${100*(data.progress||0)}%`;
 $('message').textContent=data.message;$('message').classList.toggle('error',data.status==='failed');
 $('steps').replaceChildren(...(data.steps||[]).map((s,i)=>node('li',s,i===data.stage_index?'current':i<data.stage_index?'done':'')));
 $('pattern-count').textContent=data.stage==='Gray code'?`${data.pattern_count||0} / ${data.pattern_total||44}`:(data.images?.pattern?.size||[1920,1080]).join(' × ');
 $('poses').replaceChildren(...(data.views||[]).map((v,i)=>{const b=node('button',String(i+1),`${v.role==='holdout'?'holdout':''} ${v.name===selected?'selected':''}`);b.title=scene?`Cámara, posición ${i+1}`:`${v.name}: ${v.corners} esquinas, ${v.role}`;b.onclick=()=>{selected=v.name;update(data);};return b;}));
 const fit=data.calibration;$('fit-state').textContent=fit?(fit.status==='candidate'?'Candidato':'Revisar ajuste'):'Pendiente';
 $('fit-state').classList.toggle('error',fit?.status==='rejected');
 $('fit-summary').textContent=fit?`Baseline ${fixed(fit.baseline_mm,1)} mm · RMS cámara ${fixed(fit.rms_px.cam)} px · proyector ${fixed(fit.rms_px.proj)} px`:'Sin parámetros estimados.';
 $('estimate-note').textContent=data.approximate?data.approximation_note:scene?(fit?`${data.cloud?.points?.length||0} puntos estimados. Escala de referencia sintética; sin validación física.`:'Capturando tres vistas de la misma escena. No hace falta hoja ni tablero.'):(fit?`${(data.planes||[]).length} planos estimados. ${fit.issues.length?fit.issues.join(', '):'Validación física pendiente.'}`:'La estimación aparece al reunir cuatro poses.');
 $('residuals').replaceChildren(...(data.planes||[]).map(p=>{const tr=document.createElement('tr');tr.append(node('td',p.name.replace('pose_','')),node('td',fixed(p.residual_camera_px)),node('td',fixed(p.residual_projector_px)));return tr;}));
 if(data.validation){const values=data.validation.views.map(v=>v.dimension_error_mm?.max).filter(Number.isFinite);$('holdout-note').textContent=`${data.validation.views.length} controles reservados. Máximo dimensional: ${values.length?fixed(Math.max(...values),2)+' mm':'sin medir'}. Sin corrección de escala.`;}
 else $('holdout-note').textContent='';
 if(scene&&data.scene_validation){const s=data.scene_validation.chosen,obs=s.report.observability;$('holdout-note').textContent=`XYZ reservado: máximo ${fixed(s.heldout_truth.xyz_mm.max,2)} mm · p95 ${fixed(s.heldout_truth.xyz_mm.p95,2)} mm. Escala sintética exacta. Rango de datos ${obs.data_rank}/${obs.global_parameters}. ${s.heldout_truth.passed_2mm?'≤2 mm solo en esta simulación.':'No alcanza 2 mm.'}`;$('residuals').replaceChildren(...data.scene_validation.comparison.map(v=>{const tr=document.createElement('tr');tr.append(node('td',`${v.views} vistas · prior ${v.prior_strength}`),node('td',fixed(v.report.rms_px.cam)),node('td',fixed(v.report.rms_px.proj)));return tr;}));}
 if(data.model){const m=data.model.model;$('model-description').replaceChildren(node('p',`${data.model.camera_size.join(' × ')} cámara · ${data.model.projector_size.join(' × ')} proyector · tiro ${m.throw_ratio} · latencia modelada ${m.latency_ms} ms.`),...data.model.assumptions.map(s=>node('p',s)));}
 $('restart').disabled=data.status==='running';$('pause').disabled=data.status!=='running';
 updateImages();updateGeometry();
}
let lastWidth=0,lastHeight=0;
function animate(){requestAnimationFrame(animate);const w=canvas.clientWidth,h=canvas.clientHeight;if(w!==lastWidth||h!==lastHeight){renderer.setSize(w,h,false);orbit.aspect=w/h;orbit.updateProjectionMatrix();lastWidth=w;lastHeight=h;}if(mode==='orbit')controls.update();renderer.setScissorTest(false);renderer.setViewport(0,0,w,h);renderer.clear();if(mode==='camera'&&cameraData()){const d=cameraData(),ratio=d.size[0]/d.size[1];let vw=Math.min(w,h*ratio),vh=vw/ratio;renderer.setViewport((w-vw)/2,(h-vh)/2,vw,vh);renderer.render(world,lens);}else renderer.render(world,orbit);}
animate();$('boot').hidden=true;window.viewerReady=true;
if(new URLSearchParams(location.search).get('view')==='camera')setMode('camera');
const events=new EventSource('/api/events');events.onmessage=e=>{try{update(JSON.parse(e.data));$('connection').textContent='En vivo';}catch(err){$('message').textContent=err.message;$('message').classList.add('error');}};events.onerror=()=>{$('connection').textContent='Reconectando…';};
async function action(name,body={}){const r=await fetch('/api/simulation/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw new Error((await r.json()).detail||'No se pudo cambiar la simulación');}
$('pause').onclick=async()=>{try{await action(paused?'resume':'pause');paused=!paused;$('pause').textContent=paused?'Continuar simulación':'Pausar simulación';}catch(e){$('control-note').textContent=e.message;}};
$('restart').onclick=async()=>{try{await action('start',{read_noise_dn:+$('noise').value,camera_blur_px:+$('blur').value,throw_ratio:+$('throw').value});paused=false;$('pause').textContent='Pausar simulación';}catch(e){$('control-note').textContent=e.message;}};
for(const [id,suffix] of [['noise',' DN'],['blur',' px'],['throw','']])$(id).oninput=()=>$(id+'-out').textContent=Number($(id).value).toFixed(2)+suffix;
