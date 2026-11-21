// Pure row-major math used by both Three.js and the OpenCV cross-check tests.
export function cameraMatrices(d, size=d.size, offset=[0,0], near=1, far=20000) {
  const [w,h]=size, K=d.K, cx=K[0][2]-offset[0], cy=K[1][2]-offset[1];
  const projection=[2*K[0][0]/w,0,1-2*(cx+.5)/w,0,
    0,2*K[1][1]/h,2*(cy+.5)/h-1,0, 0,0,-(far+near)/(far-near),-2*far*near/(far-near), 0,0,-1,0];
  const b=[1,-1,-1,1], view=d.T_device_from_world.flatMap((r,i)=>r.map((v,j)=>b[i]*v*b[j]));
  return {projection,view};
}
export function transform(m,p) {return [0,1,2,3].map(i=>p.reduce((s,v,j)=>s+m[4*i+j]*v,0));}
export function idealPixel(d,point) {
  const {projection,view}=cameraMatrices(d);
  const clip=transform(projection,transform(view,[point[0],-point[1],-point[2],1]));
  return [(clip[0]/clip[3]+1)*d.size[0]/2-.5,(1-clip[1]/clip[3])*d.size[1]/2-.5];
}
