"""Normalize real Q-static-pressure points; optional display-only PCHIP samples."""
from pathlib import Path
import argparse,csv,json,math,hashlib,sys

def number(value,label):
    if isinstance(value,bool):raise ValueError(f'{label} cannot be boolean')
    result=float(value)
    if not math.isfinite(result):raise ValueError(f'{label} must be finite')
    return result
def first(record,keys):
    for key in keys:
        if key in record:return record[key]
    raise ValueError('Missing field: '+ '/'.join(keys))
def normalize(points,mode):
    rows=[]
    for point in points:
        qh=number(first(point,['single_arm_flow_m3h','flow_m3h']),'flow m3/h')
        if qh<0:raise ValueError('Volumetric flow must be nonnegative')
        qs=qh/3600.
        for key in ['single_arm_flow_m3s','flow_m3s']:
            if key in point and not math.isclose(number(point[key],key),qs,rel_tol=1e-9,abs_tol=1e-12):
                raise ValueError('Inconsistent m3/h and m3/s flow units')
        pressure=number(first(point,['static_pressure_difference_Pa','static_port_pressure_difference_Pa','static_delta_p_Pa']),'pressure Pa')
        converged=point.get('numerically_converged',point.get('converged')) is True
        reopened=point.get('native_reopen_verified') is True
        if mode=='final' and not (converged and reopened and point.get('source_directory')):
            raise ValueError(f'Flow {qh}: final mode requires converged, native_reopen_verified and source_directory evidence metadata')
        rows.append({'flow_m3h':qh,'flow_m3s':qs,'static_delta_p_Pa':pressure,
            'numerically_converged':converged,'native_reopen_verified':reopened,
            'status':'FINAL_METADATA_CHECKED' if mode=='final' else 'PROVISIONAL_NOT_VALIDATED',
            'native_iteration':point.get('native_iteration',''),'source_directory':point.get('source_directory','')})
    rows.sort(key=lambda row:row['flow_m3h'])
    if not rows:raise ValueError('No real flow points supplied')
    if any(math.isclose(a['flow_m3h'],b['flow_m3h'],rel_tol=1e-10,abs_tol=1e-12) for a,b in zip(rows,rows[1:])):
        raise ValueError('Duplicate flow points: iteration history is not a performance curve')
    return rows
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('points',type=Path,help='JSON list or envelope with points/rows')
    parser.add_argument('--mode',choices=['provisional','final'],required=True)
    parser.add_argument('--output',type=Path,required=True,help='Dual-unit CSV containing actual points only')
    parser.add_argument('--smooth-output',type=Path,help='Separate JSON of display-only interpolation samples')
    parser.add_argument('--samples',type=int,default=401)
    parser.add_argument('--deps-dir',type=Path,help='Compatible NumPy/SciPy directory if needed')
    parser.add_argument('--force',action='store_true',help='Replace only the named generated outputs')
    args=parser.parse_args()
    if args.output.suffix.lower()!='.csv':raise ValueError('Actual point output must be a separate .csv file')
    if args.smooth_output and args.smooth_output.suffix.lower()!='.json':raise ValueError('Display interpolation output must be a separate .json file')
    raw=args.points.read_bytes();payload=json.loads(raw.decode('utf-8-sig'))
    points=payload if isinstance(payload,list) else payload.get('points',payload.get('rows',[]))
    rows=normalize(points,args.mode)
    destinations=[args.output]+([args.smooth_output] if args.smooth_output else [])
    if len({p.resolve() for p in destinations})!=len(destinations):raise ValueError('Output files must be distinct')
    for target in destinations:
        if target.resolve()==args.points.resolve():raise ValueError('Do not overwrite source points')
        if target.exists() and not args.force:raise ValueError(f'Output exists; choose a new file or use --force: {target}')
    smoothing=None
    if args.smooth_output:
        if len(rows)<2 or args.samples<2:raise ValueError('At least two distinct flow points/samples are needed for interpolation')
        if args.deps_dir:sys.path.insert(0,str(args.deps_dir))
        import numpy as np
        from scipy.interpolate import PchipInterpolator
        x=np.array([r['flow_m3h'] for r in rows]);y=np.array([r['static_delta_p_Pa'] for r in rows])
        interpolator=PchipInterpolator(x,y,extrapolate=False);sample_x=np.linspace(x[0],x[-1],args.samples)
        if not np.allclose(interpolator(x),y,rtol=0,atol=1e-8):raise ValueError('Interpolator failed original-point check')
        sample_y=interpolator(sample_x)
        smoothing={'kind':'DISPLAY_INTERPOLATION_NOT_CFD_POINTS','method':'PCHIP','extrapolation':False,
            'source_points_sha256':hashlib.sha256(raw).hexdigest(),'actual_point_count':len(rows),
            'samples':[{'flow_m3h':float(q),'flow_m3s':float(q/3600.),'display_static_delta_p_Pa':float(p)} for q,p in zip(sample_x,sample_y)]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    if smoothing:
        args.smooth_output.parent.mkdir(parents=True,exist_ok=True)
        args.smooth_output.write_text(json.dumps(smoothing,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'actual_points':len(rows),'mode':args.mode,'csv':str(args.output),
        'smooth_output':str(args.smooth_output) if args.smooth_output else None,
        'scope':'Final mode checks metadata only; the caller must independently verify native evidence.'},ensure_ascii=False))
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,KeyError,OSError,ImportError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);raise SystemExit(1)
