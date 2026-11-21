"""Physical control primitives. No offsets, rescaling or hidden trimming."""
import numpy as np
from scipy.spatial import cKDTree
from .joint import intersect, stats, triangulate


def outline_on_plane(camera, physical_pixels, projected_line_pixels, plane_normal,
                     plane_point_mm, *, covered_length_mm, expected_length_mm,
                     uncertainty_mm=None):
    """Bidirectional sampled boundary distance using local calibrated ray/plane.

    Inputs must be independently detected in a white frame and a white frame
    with black contours. A caller must declare coverage and uncertainty before
    claiming a pass. This measures the supplied samples, not unsampled curves.
    """
    n = np.asarray(plane_normal,float)
    if n.shape!=(3,) or not np.isfinite(n).all() or np.linalg.norm(n)<1e-8:
        raise ValueError('normal de superficie inválida')
    if expected_length_mm<=0 or not 0<=covered_length_mm<=expected_length_mm:
        raise ValueError('cobertura de borde inválida')
    if uncertainty_mm is not None and (not np.isfinite(uncertainty_mm) or uncertainty_mm<0):
        raise ValueError('incertidumbre inválida')
    R = np.eye(3); R[:,2] = n/np.linalg.norm(n)
    points=[]
    for pixels in (physical_pixels,projected_line_pixels):
        pixels=np.asarray(pixels,float)
        if pixels.ndim!=2 or pixels.shape[1]!=2 or len(pixels)<2 or not np.isfinite(pixels).all():
            raise ValueError('faltan muestras independientes de ambos bordes')
        X,depth=intersect(camera,pixels,R,np.asarray(plane_point_mm,float))
        if not np.isfinite(X).all() or np.any(depth<=0):
            raise ValueError('rayos no intersectan la superficie delante de la cámara')
        points.append(X)
    A,B=points
    distance=np.r_[cKDTree(A).query(B)[0],cKDTree(B).query(A)[0]]
    result=stats(distance)
    coverage=covered_length_mm/expected_length_mm
    status='failed' if result['max']>2 else 'inconclusive'
    if coverage==1 and uncertainty_mm is not None and result['max']+uncertainty_mm<=2:
        status='passed'
    return {'status':status,'residual_mm':result,'coverage':coverage,
            'uncertainty_mm':uncertainty_mm,'covered_length_mm':covered_length_mm,
            'expected_length_mm':expected_length_mm,'registration_correction_applied':False,
            'scope':'sampled independent physical and projected boundary on this plane'}


def pixel_uncertainty(rig, camera_pixels, projector_pixels, sigma_px=.25):
    """Numerical 3D propagation of pixel noise, conditional on a fixed rig.

    Reports one-sigma axis/3D values, not maximum error or total uncertainty.
    Calibration and reference uncertainty must be evaluated separately.
    """
    if not np.isfinite(sigma_px) or sigma_px<=0:
        raise ValueError('sigma_px debe ser positivo')
    cam,pro=rig[rig.world],rig.projector()
    xy=np.column_stack((camera_pixels,projector_pixels)).astype(float)
    J=np.empty((len(xy),3,4))
    for k in range(4):
        a,b=xy.copy(),xy.copy(); a[:,k]+=.001; b[:,k]-=.001
        J[:,:,k]=(triangulate(cam,pro,a[:,:2],a[:,2:])-triangulate(cam,pro,b[:,:2],b[:,2:]))/.002
    covariance=J@np.swapaxes(J,1,2)*sigma_px**2
    return {'kind':'pixel_noise_only_conditional_on_fixed_rig','sigma_px':sigma_px,
            'sigma_xyz_mm':np.sqrt(np.maximum(0,np.diagonal(covariance,axis1=1,axis2=2))),
            'sigma_3d_mm':np.sqrt(np.maximum(0,np.trace(covariance,axis1=1,axis2=2)))}
