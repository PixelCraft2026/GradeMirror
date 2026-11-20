import math,time

import numpy as np

import scipy

from scipy import sparse

from scipy.sparse.linalg import cg,LinearOperator

def area_axis(n,r):
 rows=[];cols=[];data=[]
 for q in range(r):
  low=q*n/r;high=(q+1)*n/r
  for p in range(math.floor(low),min(n,math.ceil(high))):
   a=max(0.,min(high,p+1)-max(low,p))/(n/r)
   if a:rows.append(q);cols.append(p);data.append(a)
 return sparse.csr_matrix((data,(rows,cols)),shape=(r,n),dtype=np.float64)

def hats_axis(n,r):
 u=(np.arange(n,dtype=np.float64)+.5)*r/n/8;i=np.floor(u).astype(np.int64);f=u-i;rows=np.repeat(np.arange(n),2);cols=np.column_stack((i,i+1)).ravel();data=np.column_stack((1-f,f)).ravel();return sparse.csr_matrix((data,(rows,cols)),shape=(n,math.ceil(r/8)+1))

def geometry(wh,rwh):
 w,h=wh;rw,rh=rwh;nx=math.ceil(rw/8)+1;ny=math.ceil(rh/8)+1;k=nx*ny
 ax=area_axis(w,rw)@hats_axis(w,rw);ay=area_axis(h,rh)@hats_axis(h,rh);a=sparse.kron(ay,ax,format='csr');edges=[]
 for y in range(ny):
  for x in range(nx):
   i=y*nx+x
   if x+1<nx:edges.append((i,i+1))
   if y+1<ny:edges.append((i,i+nx))
 er=np.repeat(np.arange(len(edges)),2);ec=np.asarray(edges).ravel();ed=np.tile([1.,-1.],len(edges));d=sparse.csr_matrix((ed,(er,ec)),shape=(len(edges),k));n=rw*rh
 q=(a.T@a)/n+sparse.eye(k,format='csr')*(.0001/k)+(d.T@d)*(.01/len(edges));q=q.tocsr();diag=q.diagonal();diff=q-q.T
 assert np.isfinite(q.data).all() and np.all(diag>0) and (diff.nnz==0 or np.max(abs(diff.data))<1e-14),'NORMAL_MATRIX'
 assert np.max(abs(np.asarray(a.sum(axis=1)).ravel()-1))<=1e-10,'PARTITION_OF_UNITY'
 return dict(A=a,Q=q,D=d,diag=diag,K=k,N=n,E=len(edges),nx=nx,ny=ny)

def objective(g,b,residual):
 e=g['A']@b-residual;return float(e@e/g['N']+.0001*(b@b)/g['K']+.01*np.sum((g['D']@b)**2)/g['E'])

def solve(g,hz,target,L):
 hz=np.asarray(hz,np.float64).reshape(-1);target=np.asarray(target,np.float64).reshape(-1);assert hz.shape==target.shape==(g['N'],) and np.isfinite(hz).all() and np.isfinite(target).all()
 residual=target-hz;rhs=g['A'].T@residual/g['N'];counter=[0];start=time.perf_counter();L.take(scalar_CG=1)
 def callback(x):
  L.check();counter[0]+=1
  if time.perf_counter()-start>300:raise TimeoutError('CG_300S')
 m=LinearOperator(g['Q'].shape,matvec=lambda x:x/g['diag'],dtype=np.float64);b,info=cg(g['Q'],rhs,x0=np.zeros(g['K'],np.float64),rtol=1e-8,atol=1e-12,maxiter=2000,M=m,callback=callback)
 norm=float(np.linalg.norm(rhs-g['Q']@b));gate=max(1e-8*float(np.linalg.norm(rhs)),1e-12);j=objective(g,b,residual);j0=float(np.mean(residual**2));row=dict(info=int(info),iterations=counter[0],seconds=time.perf_counter()-start,residual_norm=norm,residual_gate=gate,J=j,J0=j0,K=g['K'],N=g['N'],E=g['E'],rhs_norm=float(np.linalg.norm(rhs)),zero_rhs=bool(np.count_nonzero(rhs)==0))
 assert info==0 and np.isfinite(b).all() and norm<=gate and j<=j0+1e-10+1e-8*abs(j0),('CG_GATE',row)
 if row['zero_rhs']:assert np.max(abs(b))<=1e-12 and counter[0]==0
 return b,row

