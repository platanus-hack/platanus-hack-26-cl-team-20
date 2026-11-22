"""Proyecta planetas como esferas 3D reales (trazado de rayos en WebGL) sobre las bolas: cada píxel del proyector se lleva a
mm de la mesa, se lanza un rayo desde el centro óptico y se intersecta con los modelos de out/modelos.json. La luz viene del Sol."""
import json, subprocess, time, sys
import numpy as np
S='/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
met=json.load(open('fp-metric.json')); D=json.load(open('out/modelos.json')); C2T=np.array(met['canvas_to_mm']); cc=D['camara']
esf=[m for m in D['modelos'] if m['tipo']=='esfera']; sol=[m for m in esf if 'Sol' in m['etiqueta']]; pl=sorted([m for m in esf if 'Sol' not in m['etiqueta']],key=lambda m:-m['radio'])
# reparto por tamaño: gigantes gaseosos a las grandes, rocosos a las chicas  (tipo: 1 Júpiter 2 Saturno 3 Urano 4 Neptuno 5 Tierra 6 Venus 7 Marte 8 Mercurio 9 Luna)
orden=[1,2,3,4,5,6,7,8,9,9,9,9]; sph=[]
for m,t in zip(pl,orden): sph.append((m['centro'],m['radio'],t,m['etiqueta']))
if sol: sph.append((sol[0]['centro'],sol[0]['radio'],0,'Sol'))
sunp=sol[0]['centro'] if sol else [80,520,40]
N=len(sph); U="\n".join(f"  S[{i}]=vec4({c[0]:.1f},{c[1]:.1f},{c[2]:.1f},{r*0.93:.1f}); K[{i}]={float(t):.1f};" for i,(c,r,t,_) in enumerate(sph))
m=C2T/C2T[2,2]
frag=f"""precision highp float; uniform float T; const int N={N};
vec4 S[{N}]; float K[{N}];
float h(vec2 p){{return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}}
float n2(vec2 p){{vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}}
float fbm(vec2 p){{float a=.5,s=0.;for(int i=0;i<4;i++){{s+=a*n2(p);p*=2.1;a*=.5;}}return s;}}
vec3 alb(float k,float lon,float lat){{
  vec2 q=vec2(lon*1.6,lat*3.2);
  if(k<0.5) return vec3(1.,.75,.25)*(0.8+0.5*fbm(q*3.+T*.6));
  if(k<1.5) {{float b=sin(lat*14.+2.*fbm(q*2.))*.5+.5; vec3 c=mix(vec3(.85,.62,.42),vec3(.98,.9,.78),b); float sp=smoothstep(.25,.0,length(vec2(mod(lon+3.14,6.28)-2.,lat+.35)*vec2(.6,1.6))); return mix(c,vec3(.8,.25,.15),sp);}}
  if(k<2.5) {{float b=sin(lat*10.)*.5+.5; return mix(vec3(.9,.78,.5),vec3(1.,.93,.72),b);}}
  if(k<3.5) return vec3(.55,.9,.95)*(.9+.15*sin(lat*6.));
  if(k<4.5) return vec3(.25,.42,1.)*(.85+.3*fbm(q*2.));
  if(k<5.5) {{float l=smoothstep(.5,.56,fbm(q*1.7+3.)); vec3 c=mix(vec3(.08,.3,.85),vec3(.2,.7,.25),l); float cl=smoothstep(.55,.75,fbm(q*3.+vec2(T*.15,0.))); return mix(c,vec3(1.),cl*.8);}}
  if(k<6.5) return vec3(.98,.82,.5)*(.8+.3*fbm(q*2.5+T*.1));
  if(k<7.5) return vec3(.95,.38,.2)*(.7+.45*fbm(q*3.));
  if(k<8.5) return vec3(.7,.66,.62)*(.6+.6*fbm(q*5.));
  return vec3(.85)*(.55+.6*fbm(q*6.));
}}
void main(){{
{U}
  vec2 c=vec2(gl_FragCoord.x/1920.*1000.,(1.-gl_FragCoord.y/1080.)*563.);
  vec3 t=vec3({m[0,0]:.9g}*c.x+{m[0,1]:.9g}*c.y+{m[0,2]:.9g},{m[1,0]:.9g}*c.x+{m[1,1]:.9g}*c.y+{m[1,2]:.9g},{m[2,0]:.9g}*c.x+{m[2,1]:.9g}*c.y+1.);
  vec3 P=vec3(t.xy/t.z,0.), O=vec3({cc[0]:.1f},{cc[1]:.1f},{cc[2]:.1f}), d=normalize(P-O);
  float best=1e9; vec3 col=vec3(0.); vec3 sun=vec3({sunp[0]:.1f},{sunp[1]:.1f},{sunp[2]+20:.1f});
  for(int i=0;i<N;i++){{
    vec3 oc=O-S[i].xyz; float b=dot(oc,d), q=dot(oc,oc)-S[i].w*S[i].w, D=b*b-q;
    if(D>0.){{ float s=-b-sqrt(D); if(s>0.&&s<best){{ best=s; vec3 p=O+d*s; vec3 n=normalize(p-S[i].xyz);
      float lon=atan(n.y,n.x)+T*(.25+.07*float(i)), lat=asin(clamp(n.z,-1.,1.));
      vec3 a=alb(K[i],lon,lat);
      if(K[i]<0.5) col=a*1.2;
      else {{ vec3 L=normalize(sun-p); float df=max(dot(n,L),0.); col=a*(.32+0.95*df); }}
    }} }}
  }}
  gl_FragColor=vec4(col,1.);
}}"""
js="""const c=document.getElementById('c');c.width=1920;c.height=1080;const g=c.getContext('webgl');
const sh=(t,s)=>{const o=g.createShader(t);g.shaderSource(o,s);g.compileShader(o);if(!g.getShaderParameter(o,g.COMPILE_STATUS))document.title='ERR '+g.getShaderInfoLog(o);return o};
const p=g.createProgram();g.attachShader(p,sh(g.VERTEX_SHADER,'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}'));g.attachShader(p,sh(g.FRAGMENT_SHADER,__SRC__));g.linkProgram(p);g.useProgram(p);
g.bindBuffer(g.ARRAY_BUFFER,g.createBuffer());g.bufferData(g.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),g.STATIC_DRAW);const l=g.getAttribLocation(p,'a');g.enableVertexAttribArray(l);g.vertexAttribPointer(l,2,g.FLOAT,false,0,0);
const u=g.getUniformLocation(p,'T');const f=t=>{g.uniform1f(u,t/1000);g.drawArrays(g.TRIANGLE_STRIP,0,4);requestAnimationFrame(f)};requestAnimationFrame(f);""".replace('__SRC__',json.dumps(frag))
w={"type":"effect","id":"v2-planetas3d","shader":"html","rect_mm":[0,0,1000,563],"params":{"html":'<canvas id="c"></canvas>',"css":"html,body{margin:0;background:#000;overflow:hidden}canvas{position:absolute;left:0;top:0;width:1000px;height:563px}","js":js}}
def pcx(t,a): return subprocess.run(["/usr/bin/python3",S,t,json.dumps(a)],capture_output=True,text=True).stdout
sc=json.loads(pcx('scene',{})); old=[x['id'] for x in sc.get('widgets',[]) if x['id'].startswith('v2-')]
if old: pcx('remove',{"request_id":"v2rm-%d"%time.time(),"ids":old})
print(pcx('project',{"request_id":"v2p3d-%d"%time.time(),"widgets":[w]})[:120]); print('esferas:',[(e,t) for _,_,t,e in sph])
