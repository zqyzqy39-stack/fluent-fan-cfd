"""Read-only numerical checks for single-condition SST checkpoint histories."""
from pathlib import Path
import argparse,json,math,sys

EQUATIONS=['continuity','x-velocity','y-velocity','z-velocity','k','omega']
def finite(value,name):
    if isinstance(value,bool):raise ValueError(f'{name} must be numeric, not boolean')
    number=float(value)
    if not math.isfinite(number):raise ValueError(f'{name} must be finite')
    return number
def residuals(record):
    values=record.get('residuals')
    if isinstance(values,dict):values=[values[name] for name in EQUATIONS]
    if not isinstance(values,list) or len(values)!=6:raise ValueError('Exactly six SST residuals are required')
    result=[finite(v,'residual') for v in values]
    if min(result)<0:raise ValueError('Residuals cannot be negative')
    return result
def second_order(record):
    return str(record.get('order','')).lower() in ['second-order','second_order','second order']
def evaluate(history,*,origin=0,residual_limit=1e-4,mass_limit=.001,
             pressure_limit=.005,window=3,min_iterations=400):
    if window<2:raise ValueError('Pressure window must contain at least two checkpoints')
    if not all(math.isfinite(v) and v>0 for v in [residual_limit,mass_limit,pressure_limit]):raise ValueError('Limits must be finite and positive')
    if origin<0 or min_iterations<0:raise ValueError('Iteration settings cannot be negative')
    filtered=[r for r in history if int(r['native_iteration'])>origin]
    if not filtered:raise ValueError('No checkpoint after the specified iteration origin')
    iterations=[int(r['native_iteration']) for r in filtered]
    if any(b<=a for a,b in zip(iterations,iterations[1:])):raise ValueError('Native iterations must strictly increase')
    flows={float(r['single_arm_flow_m3h'] if 'single_arm_flow_m3h' in r else r['flow_m3h'])
           for r in filtered if 'single_arm_flow_m3h' in r or 'flow_m3h' in r}
    if len(flows)>1:raise ValueError('Mixed flow conditions must not share a convergence window')
    last=filtered[-1];values=residuals(last)
    local=int(last.get('local_iteration',last.get('local_iterations',iterations[-1]-origin)))
    if local<0 or local>iterations[-1]-origin:raise ValueError('Inconsistent local/native iteration counts')
    mass=finite(last['mass_imbalance_fraction'],'mass imbalance')
    if mass<0:raise ValueError('Mass imbalance fraction cannot be negative')
    recent=filtered[-window:];enough=len(recent)==window
    pressure=[finite(r['static_pressure_difference_Pa'],'static pressure difference') for r in recent]
    stability=(max(pressure)-min(pressure))/max(abs(sum(pressure)/len(pressure)),1.) if enough else None
    gates={'second_order':second_order(last),'minimum_local_iterations':local>=min_iterations,
           'all_six_residuals':max(values)<residual_limit,'mass_balance':mass<mass_limit,
           'enough_pressure_checkpoints':enough,'pressure_window_all_second_order':enough and all(map(second_order,recent)),
           'pressure_stability':enough and stability<pressure_limit}
    return {'numerically_converged':all(gates.values()),'gates':gates,
            'native_iteration':iterations[-1],'local_iteration':local,
            'residuals':dict(zip(EQUATIONS,values)),'maximum_scaled_residual':max(values),
            'mass_imbalance_fraction':mass,'pressure_range_fraction':stability,
            'pressure_checkpoints_used':len(recent),'limits':{'residual':residual_limit,'mass':mass_limit,
                'pressure':pressure_limit,'window':window,'minimum_local_iterations':min_iterations},
            'native_data_checked':False,'native_data_matches':None,
            'scope':'Numerical evidence only; not geometry, native pair reopening, grid independence or experimental validation.'}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('history',type=Path,help='Single-condition JSON list, history envelope, run status, or checkpoint')
    parser.add_argument('--iteration-origin',type=int,default=0,help='Parent native iteration before this flow was started')
    parser.add_argument('--residual-limit',type=float,default=1e-4)
    parser.add_argument('--mass-limit',type=float,default=.001,help='Fraction, not percent')
    parser.add_argument('--pressure-limit',type=float,default=.005,help='Fraction, not percent')
    parser.add_argument('--window',type=int,default=3)
    parser.add_argument('--min-iterations',type=int,default=400)
    parser.add_argument('--native-data',type=Path,help='Optional saved, inactive Fluent Data HDF5 residual cross-check')
    parser.add_argument('--deps-dir',type=Path,help='Compatible h5py dependency directory if needed')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--require-converged',action='store_true',help='Exit 2 for a valid but unconverged history')
    args=parser.parse_args()
    if args.output:
        inputs={args.history.resolve()}
        if args.native_data:inputs.add(args.native_data.resolve())
        if args.output.resolve() in inputs:raise ValueError('Do not overwrite source history or Native Data')
        if args.output.suffix.lower()!='.json':raise ValueError('Review output must be a separate .json file')
    payload=json.loads(args.history.read_text(encoding='utf-8-sig'))
    if isinstance(payload,list):history=payload
    elif 'history' in payload:history=payload['history']
    elif 'last_checkpoint' in payload:history=[payload['last_checkpoint']]
    else:history=[payload]
    result=evaluate(history,origin=args.iteration_origin,residual_limit=args.residual_limit,
        mass_limit=args.mass_limit,pressure_limit=args.pressure_limit,window=args.window,min_iterations=args.min_iterations)
    if args.native_data:
        if args.deps_dir:sys.path.insert(0,str(args.deps_dir))
        import h5py
        with h5py.File(args.native_data,'r') as file:
            group=file['results/residuals/phase-1'];native=[];native_iterations=[]
            for equation in EQUATIONS:
                native_iterations.append(int(group[equation+'/iterations'][-1]))
                numerator,denominator=group[equation+'/data'][-1,:2]
                if denominator==0:raise ValueError('Native residual normalization denominator is zero')
                native.append(finite(numerator/denominator,'native scaled residual'))
        expected=list(result['residuals'].values())
        matches=all(it==result['native_iteration'] for it in native_iterations) and all(
            math.isclose(a,b,rel_tol=.002,abs_tol=1e-12) for a,b in zip(native,expected))
        result.update(native_data_checked=True,native_data_matches=matches,native_data_residuals=dict(zip(EQUATIONS,native)))
        result['gates']['native_data_matches']=matches
        result['gates']['native_all_six_residuals']=max(native)<args.residual_limit
        result['numerically_converged']=all(result['gates'].values())
    rendered=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(rendered,encoding='utf-8')
    print(rendered)
    return 2 if args.require_converged and not result['numerically_converged'] else 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,KeyError,OSError,ImportError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);raise SystemExit(1)
