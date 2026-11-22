import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {cameraMatrices} from './registered-math.mjs';

const $=id=>document.getElementById(id);
const checked=async response=>{if(!response.ok)throw Error(await response.text());return response;};
const state={ready:false,frames:0}; window.registered=state;

async function start(){
 const data=await (await checked(await fetch('/api/registered'))).json();
 const [w,h]=data.devices[data.projector].size, [iw,ih]=data.optics.size;
 const scene=new THREE.Scene(), geometry=new THREE.BufferGeometry();
 const a=data.geometry;
 geometry.setAttribute('position',new THREE.Float32BufferAttribute(a.vertices_mm.flatMap(p=>[p[0],-p[1],-p[2]]),3));
 geometry.setAttribute('uv',new THREE.Float32BufferAttribute(a.uv.flat(),2));
 const faces=a.faces.filter(face=>face.every(i=>a.measured[i]));
 geometry.setIndex(faces.flat()); geometry.computeVertexNormals();
 // P3 UV is top-left. Texture upload is deliberately not flipped.
 const loader=new THREE.TextureLoader();
 async function texture(url,colorSpace){const t=await loader.loadAsync(url);t.flipY=false;t.colorSpace=colorSpace;t.generateMipmaps=false;t.minFilter=THREE.LinearFilter;return t;}
 const appearance=new THREE.MeshBasicMaterial({color:0xdddddd,side:THREE.DoubleSide});
 if(data.texture){appearance.map=await texture(data.texture,THREE.SRGBColorSpace);appearance.color.set(0xffffff);}
 else if(data.colors){geometry.setAttribute('color',new THREE.Float32BufferAttribute(data.colors.flatMap(p=>new THREE.Color().setRGB(...p.map(v=>v/255),THREE.SRGBColorSpace).toArray()),3));appearance.vertexColors=true;}
 const white=new THREE.MeshBasicMaterial({color:0xffffff,side:THREE.DoubleSide});
 const mesh=new THREE.Mesh(geometry,appearance);scene.add(mesh);
 // Texture-invalid faces retain geometry/depth but receive no photographic sample.
 const invalid=new THREE.MeshBasicMaterial({color:0x555555,side:THREE.DoubleSide});
 const textureValid=data.texture_valid;
 const appearanceMaterials=[appearance,invalid];
 geometry.clearGroups(); faces.forEach((f,i)=>geometry.addGroup(3*i,3,f.every(v=>textureValid[v])?0:1));
 mesh.material=appearanceMaterials;
 const pbr=new THREE.MeshStandardMaterial({color:0xffffff,side:THREE.DoubleSide,roughness:1});
 const measuredMaterials=data.materials.filter(m=>m.observation!=='unobserved');
 // No face-to-material mapping exists in v1. Only a single global UV material
 // can be applied without inventing which surface owns a material.
 const material=measuredMaterials.length===1?measuredMaterials[0]:null;
 if(material){for(const item of Object.values(material.maps)){
   const map=await texture(item.url,item.color_space==='sRGB'?THREE.SRGBColorSpace:THREE.NoColorSpace);
   if(item.meaning==='albedo')pbr.map=map;
   if(item.meaning==='normal_tangent_opengl')pbr.normalMap=map;
   if(item.meaning==='roughness')pbr.roughnessMap=map;
 }}
 $('mode').querySelector('[value=pbr]').disabled=!pbr.map;
 scene.add(new THREE.HemisphereLight(0xffffff,0x556677,2));
 const lamp=new THREE.DirectionalLight(0xffffff,2);lamp.position.set(500,1000,500);scene.add(lamp);
 const helpers=new THREE.Group();scene.add(helpers);
 function calibrated(d,size=d.size,offset=[0,0]){
   const cam=new THREE.PerspectiveCamera(),m=cameraMatrices(d,size,offset);
   cam.projectionMatrix.set(...m.projection);cam.projectionMatrixInverse.copy(cam.projectionMatrix).invert();
   cam.matrixAutoUpdate=false;cam.matrix.set(...m.view).invert();cam.matrix.decompose(cam.position,cam.quaternion,cam.scale);cam.updateMatrixWorld(true);return cam;
 }
 const projector=calibrated(data.devices[data.projector],data.optics.size,data.optics.offset);
 for(const d of Object.values(data.devices)){
   const cam=calibrated(d), helper=new THREE.CameraHelper(cam);
   // Frustum depth is visual only; calibration matrices used for output stay intact.
   const short= cameraMatrices(d,d.size,[0,0],1,500);
   cam.projectionMatrix.set(...short.projection);cam.projectionMatrixInverse.copy(cam.projectionMatrix).invert();helper.update();
   helper.material.color?.set(d.kind==='projector'?0xe3b66e:0x53c6db);helpers.add(helper);
   const center=new THREE.Mesh(new THREE.SphereGeometry(12,10,8),new THREE.MeshBasicMaterial({color:d.kind==='projector'?0xe3b66e:0x53c6db}));center.position.copy(cam.position);helpers.add(center);
 }
 helpers.add(new THREE.AxesHelper(150));
 const navRenderer=new THREE.WebGLRenderer({canvas:$('navigation'),antialias:true});
 navRenderer.setPixelRatio(Math.min(devicePixelRatio,2));
 const nav=new THREE.PerspectiveCamera(45,1,1,30000),box=new THREE.Box3().setFromObject(mesh),center=box.getCenter(new THREE.Vector3());
 const span=Math.max(box.getSize(new THREE.Vector3()).length(),500);
 nav.position.copy(center).add(new THREE.Vector3(span*.7,span*.5,span));
 const controls=new OrbitControls(nav,$('navigation'));controls.target.copy(center);controls.update();
 const renderer=new THREE.WebGLRenderer({canvas:$('output'),antialias:false,preserveDrawingBuffer:true});
 renderer.setPixelRatio(1);renderer.setSize(w,h,false);renderer.outputColorSpace=THREE.SRGBColorSpace;
 if(iw>renderer.capabilities.maxTextureSize||ih>renderer.capabilities.maxTextureSize)throw Error('Render exceeds GPU texture limit');
 const target=new THREE.WebGLRenderTarget(iw,ih,{minFilter:THREE.LinearFilter,magFilter:THREE.LinearFilter,depthBuffer:true});
 target.texture.colorSpace=THREE.LinearSRGBColorSpace;
 const maskTarget=new THREE.WebGLRenderTarget(iw,ih,{minFilter:THREE.NearestFilter,magFilter:THREE.NearestFilter,depthBuffer:true});
 const warpData=new Float32Array(await (await checked(await fetch(data.warp_url))).arrayBuffer());
 if(warpData.length!==w*h*2)throw Error('Invalid optical map');
 const warp=new THREE.DataTexture(warpData,w,h,THREE.RGFormat,THREE.FloatType);warp.needsUpdate=true;warp.minFilter=warp.magFilter=THREE.NearestFilter;
 const post=new THREE.Scene(),quadCamera=new THREE.OrthographicCamera(-1,1,1,-1,0,1);
 const postMaterial=new THREE.ShaderMaterial({uniforms:{source:{value:target.texture},mask:{value:maskTarget.texture},warp:{value:warp},size:{value:new THREE.Vector2(w,h)},mode:{value:0},restrictObject:{value:0}},
 vertexShader:'varying vec2 tex;void main(){tex=uv;gl_Position=vec4(position.xy,0.,1.);}',
 fragmentShader:`varying vec2 tex;uniform sampler2D source,mask,warp;uniform vec2 size;uniform int mode,restrictObject;
 float visible(vec2 p){vec2 q=texture2D(warp,vec2(p.x,1.-p.y)).rg;return step(.5,texture2D(mask,q).r);}
 void main(){vec2 q=texture2D(warp,vec2(tex.x,1.-tex.y)).rg;vec3 color=texture2D(source,q).rgb;
 if(mode==0 && restrictObject==1)color*=visible(tex);
 if(mode==1)color=vec3(visible(tex));
 if(mode==2){float v=visible(tex);float edge=0.;for(int x=-1;x<=1;x++)for(int y=-1;y<=1;y++)edge=max(edge,abs(v-visible(clamp(tex+vec2(float(x),float(y))/size,vec2(0.),vec2(1.)))));color=vec3(1.-edge);}
 if(mode==3)color=vec3(1.);gl_FragColor=vec4(color,1.);
 #include <colorspace_fragment>
 }`});
 post.add(new THREE.Mesh(new THREE.PlaneGeometry(2,2),postMaterial));
 // Every measured face stays in the z-buffer. Non-selected faces render black,
 // so a hidden object never shines through the foreground.
 const maskGeometry=geometry.clone(),maskMesh=new THREE.Mesh(maskGeometry,white),maskScene=new THREE.Scene();maskScene.add(maskMesh);
 const black=new THREE.MeshBasicMaterial({color:0,side:THREE.DoubleSide});
 for(const o of data.objects){const option=document.createElement('option');option.value=o.id;option.textContent=o.label||o.id;$('object').append(option);}
 function updateMask(){
   const selected=data.objects.find(o=>o.id===$('object').value), wanted=new Set(selected?.face_indices||[]);
   maskGeometry.clearGroups();let j=0;
   a.faces.forEach((face,i)=>{if(face.every(v=>a.measured[v])){maskGeometry.addGroup(j*3,3,!selected||wanted.has(i)?0:1);j++;}});
   maskMesh.material=[white,black];
 }
 updateMask();$('object').onchange=()=>{updateMask();draw();};$('mode').onchange=()=>draw();
 function draw(){
   const mode=$('mode').value;
   mesh.material=mode==='pbr'?pbr:appearanceMaterials;
   helpers.visible=false;
   renderer.setRenderTarget(target);renderer.setClearColor(0,1);renderer.clear();renderer.render(scene,projector);
   renderer.setRenderTarget(maskTarget);renderer.clear();renderer.render(maskScene,projector);
   renderer.setRenderTarget(null);postMaterial.uniforms.mode.value=({mask:1,outlines:2,white:3})[mode]||0;
   postMaterial.uniforms.restrictObject.value=$('object').value?1:0;
   renderer.render(post,quadCamera);helpers.visible=true;state.frames++;
 }
 draw();
 function navigate(){const rect=$('navigation').getBoundingClientRect();navRenderer.setSize(rect.width,rect.height,false);nav.aspect=rect.width/rect.height;nav.updateProjectionMatrix();navRenderer.setClearColor(0x17242b);navRenderer.render(scene,nav);requestAnimationFrame(navigate);}navigate();
 const blob=()=>new Promise(resolve=>$('output').toBlob(resolve,'image/png'));
 let sending=false,timer,inFlight=Promise.resolve();
 async function send(){if(!sending)return;try{draw();const frame=await blob();if(!sending)return;inFlight=fetch(`/api/registered/frame?scene_sha256=${data.scene_sha256}&seconds=3`,{method:'POST',headers:{'Content-Type':'image/png'},body:frame});await checked(await inFlight);if(sending)timer=setTimeout(send,250);}catch(e){sending=false;$('status').textContent=e.message;}}
 $('send').disabled=w!==1920||h!==1080;
 $('send').onclick=()=>{if(!sending){sending=true;send();}};
 $('stop').onclick=async()=>{sending=false;clearTimeout(timer);await inFlight.catch(()=>{});await checked(await fetch('/api/registered/stop',{method:'POST'}));};
 $('size').textContent=`${w} × ${h}`;
 $('status').textContent=`${data.scene.evidence} · ${a.vertices_mm.length} vértices · ${faces.length} caras medidas · precisión física sin verificar`;
 $('detail').textContent=`${data.scene.texture_kind} · mundo ${data.scene.world}. Frustums rectificados; la salida incluye distorsión. PBR usa iluminación de preview.`;
 Object.assign(state,{ready:true,data,renderer,scene,projector,nav,draw,blob,maskScene,maskTarget,postMaterial});
}
start().catch(e=>{$('status').textContent=e.message;state.error=e.message;console.error(e);});
