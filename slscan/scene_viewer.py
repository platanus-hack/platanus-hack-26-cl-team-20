"""Image-based scene simulation feeding the same local calibration viewer."""
import copy
import cv2
import numpy as np
from .calibration_viewer import Journal,Simulation,device_json
from .metric_session import write_json
from .scene_simulator import FurnishedRoom,grid_pattern
from .rig_simulator import SensorModel
from . import graycode,selfcalib,joint

SCENE_STEPS=['Preparar escena','Patrón sobre relieve','Gray code','Correspondencias densas','Ajuste conjunto','Validación sintética']


def reference_points(device,pixels,surfaces):
    """Truth-only ray intersections, used exclusively AFTER fitting."""
    center=device.center_world();direction=joint.rays(device,pixels)
    depth=np.full(len(pixels),np.inf)
    for s in surfaces:
        denom=direction@s['normal']
        d=np.divide((s['corners'][0]-center)@s['normal'],denom,
                    out=np.full(len(pixels),np.inf),where=abs(denom)>1e-9)
        X=center+d[:,None]*direction;local=X-s['corners'][0]
        uv=np.column_stack((local@s['u'],local@s['v']))
        good=(d>0)&(d<depth)&np.all(uv>=0,axis=1)&np.all(uv<=s['size'],axis=1)
        depth[good]=d[good]
    return center+depth[:,None]*direction


def validation(rig,points,truth,projector_pixels,reference):
    # External metric source in this experiment is an EXACT simulated baseline.
    # It is not silently passed to the optimizer or claimed to be a measurement.
    baseline=np.linalg.norm(truth.projector().t)
    target=reference_points(truth.projector(),projector_pixels,reference)
    finite=np.isfinite(target).all(1)&np.isfinite(points).all(1)
    error=np.linalg.norm(points[finite]*baseline-target[finite],axis=1)
    return {'evidence':'synthetic','alignment':'none; camera0 world fixed; exact truth baseline as external scale',
            'scale_source':'exact_simulated_baseline_only','baseline_mm':float(baseline),
            'xyz_mm':joint.stats(error),'invalid_points':int((~finite).sum()),
            'camera_focal_error_percent':float(100*(rig['osmo'].K[0,0]/truth['osmo'].K[0,0]-1)),
            'projector_focal_error_percent':float(100*(rig.projector().K[0,0]/truth.projector().K[0,0]-1)),
            'passed_2mm':bool(len(error)>0 and error.max()<=2 and finite.all()),
            'physical_verified':False}


class SceneSimulation(Simulation):
    def run(self,options):
        journal=Journal(self.root,'synthetic',3)
        journal.emit(steps=SCENE_STEPS,mode='scene',stage=SCENE_STEPS[0],
                     message='Escena con relieve. Sin A4 ni tablero. Cámara móvil, proyector fijo.')
        folder=self.root/'runs'/journal.state['run_id'];folder.mkdir(parents=True)
        try:
            room=FurnishedRoom(SensorModel(**options))
            truth=copy.deepcopy(room.rig)
            write_json(folder/'autocalib-session.json',{'evidence':'synthetic',
                'camera_size':list(truth['osmo'].size),'projector_size':list(truth.projector().size),
                'views':3,'status':'simulating','scale_source':'exact_simulated_baseline_only'})
            room.surfaces=room.room
            journal.emit(model=room.describe(),truth_scene=room.scene_json(),
                truth_devices={k:device_json(d) for k,d in room.rig.devices.items()})
            maps=[];grid=grid_pattern()
            for v in range(3):
                self.checkpoint()
                name=f'view_{v:02d}'
                journal.emit(SCENE_STEPS[0],pose=v+1,progress=v/3,
                             message=f'Vista {v+1}/3. Preparando rayos y oclusiones de la sala.')
                room.set_view(v)
                journal.emit(truth_devices={k:device_json(d) for k,d in room.rig.devices.items()})
                photo=room.render(grid)
                journal.frame('pattern',grid);journal.frame('camera',photo)
                journal.frame(name+'_camera',photo);journal.frame(name+'_pattern',grid)
                journal.state['views'].append({'name':name,'role':'scene','corners':0,
                    'camera':journal.state['images'][name+'_camera'],
                    'pattern':journal.state['images'][name+'_pattern']})
                journal.emit(SCENE_STEPS[1],message='El grid se deforma en mesa, teclas, taza, libros y paredes.')
                items=[];specs=graycode.sequence((1920,1080))
                for i,spec in enumerate(specs):
                    self.checkpoint()
                    pattern=graycode.pattern((1920,1080),spec.axis,spec.bit,spec.inverse)
                    image=room.render(pattern)
                    path=folder/f'{name}_{spec.index:02d}.png'
                    if not cv2.imwrite(str(path),image): raise IOError('no se guardó frame sintético')
                    items.append((spec,path))
                    journal.frame('pattern',pattern);journal.frame('camera',image)
                    journal.emit(SCENE_STEPS[2],pattern_count=i+1,pattern_total=len(specs),
                        progress=(v+(i+1)/len(specs)*.7)/3,message=f'Vista {v+1}: Gray {i+1}/{len(specs)}, sensor completo.')
                decoded=graycode.decode(items,skip_bits=2);maps.append(decoded)
                decoded.save(folder/(name+'_map.npz'))
                for _,path in items: path.unlink()
                journal.emit(SCENE_STEPS[3],message=f'{decoded.valid.sum():,} píxeles válidos en la vista {v+1}.')
            c,p,tracking=selfcalib.dense_tracks(maps,limit=420)
            del maps
            train=np.arange(len(p))%5!=0;test=~train
            np.savez_compressed(folder/'tracks.npz',camera=c,projector=p,train=train)
            journal.emit(SCENE_STEPS[4],message=f'Ajuste conjunto de {train.sum()} tracks. {test.sum()} reservados.',tracking=tracking)
            candidates=[]
            for count,strength in [(1,1.),(3,1.),(3,.35)]:
                self.checkpoint()
                journal.emit(message=f'Bundle: {count} vista(s), fuerza de priors {strength}. Sin geometría verdadera como entrada.')
                rig,X,report,good=selfcalib.fit(c[:count,train],p[train],truth['osmo'].size,
                                             max_nfev=250,prior_strength=strength)
                report['evidence']='synthetic'
                check=validation(rig,X,truth,p[train][good],room.room)
                reserved=joint.triangulate(rig['osmo'],rig.projector(),c[0,test],p[test])
                held=validation(rig,reserved,truth,p[test],room.room)
                candidates.append({'views':count,'prior_strength':strength,'report':report,'train_truth':check,'heldout_truth':held})
                # Selection fixed in advance: three views, nominal priors. Do not
                # choose the candidate using the reserved truth errors.
                if count==3 and strength==1:
                    chosen=(rig,X,report,check,held)
                write_json(folder/'comparison.json',candidates)
            rig,X,report,check,held=chosen
            baseline=np.linalg.norm(truth.projector().t)
            display=copy.deepcopy(rig)
            for d in display.devices.values(): d.t*=baseline
            cloud=X*baseline
            journal.frame('pattern',grid);journal.frame('camera',room.render(grid))
            write_json(folder/'selfcalibration.json',{'format':'slscan-selfcalibration/1','units':rig.units,
                'devices':{k:d.to_json() for k,d in rig.devices.items()},'report':report,
                'points':X.tolist(),'evidence':'synthetic','metric_status':'unverified'})
            journal.emit(SCENE_STEPS[5],status='completed',progress=1.,
                devices={k:device_json(d) for k,d in display.devices.items()},
                cloud={'points':cloud.tolist(),'scale_source':'exact_simulated_baseline_only'},
                calibration={'status':'unverified','issues':report['issues'],'rms_px':report['rms_px'],
                             'baseline_mm':float(baseline),'observability':report['observability']},
                scene_validation={'chosen':candidates[1],'comparison':candidates},
                message=f"Escena sin hoja: error XYZ reservado máximo {held['xyz_mm']['max']:.2f} mm con escala sintética exacta. Precisión física sin verificar.")
        except Exception as exc:
            journal.emit(status='failed',message=str(exc))
