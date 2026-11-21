import numpy as np, json, sys
cam=[(771,602),(322,527),(352,318),(803,400)]; mm=[(100,100),(900,100),(900,463),(100,463)]
A=[]
for (x,y),(u,v) in zip(cam,mm):
    A+= [[x,y,1,0,0,0,-u*x,-u*y,-u],[0,0,0,x,y,1,-v*x,-v*y,-v]]
H=np.linalg.svd(np.array(A,float))[2][-1].reshape(3,3)
np.save("H.npy",H)
def to_mm(x,y):
    p=H@[x,y,1]; return [float(round(p[0]/p[2],1)), float(round(p[1]/p[2],1))]
if __name__=="__main__":
    print(json.dumps([to_mm(float(a),float(b)) for a,b in zip(sys.argv[1::2],sys.argv[2::2])]))
