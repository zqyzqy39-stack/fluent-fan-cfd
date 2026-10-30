"""Synthetic helper tests. No customer data, Fluent session or solver license needed."""
from pathlib import Path
import copy,csv,importlib.util,json,math,os,subprocess,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
def load(name,filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/filename)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
review=load('fan_review','evaluate_convergence.py')
table=load('fan_table','curve_table.py')
def history():
    return [{'native_iteration':it,'order':'second-order','residuals':[1e-5]*6,
             'mass_imbalance_fraction':.0001,'static_pressure_difference_Pa':p,
             'single_arm_flow_m3h':60} for it,p in [(600,2000),(700,2001),(800,2002)]]
def points():
    return [{'flow_m3h':q,'static_delta_p_Pa':p,'converged':False,
             'native_reopen_verified':False,'source_directory':'synthetic'}
            for q,p in [(10,1200),(25,1150),(50,1000)]]
class NumericalTests(unittest.TestCase):
    def test_stable_history(self):self.assertTrue(review.evaluate(history())['numerically_converged'])
    def test_continuity_not_converged(self):
        h=history();h[-1]['residuals'][0]=.01
        self.assertFalse(review.evaluate(h)['numerically_converged'])
    def test_turbulence_not_converged(self):
        h=history();h[-1]['residuals'][4]=.01
        self.assertFalse(review.evaluate(h)['numerically_converged'])
    def test_strict_mass_boundary(self):
        h=history();h[-1]['mass_imbalance_fraction']=.001
        self.assertFalse(review.evaluate(h)['gates']['mass_balance'])
    def test_pressure_drift(self):
        h=history();h[-1]['static_pressure_difference_Pa']=2400
        self.assertFalse(review.evaluate(h)['gates']['pressure_stability'])
    def test_first_order(self):
        h=history();h[-1]['order']='first-order'
        self.assertFalse(review.evaluate(h)['numerically_converged'])
    def test_incomplete_pressure_window(self):self.assertFalse(review.evaluate(history()[-1:])['numerically_converged'])
    def test_parent_history_not_borrowed(self):
        result=review.evaluate(history(),origin=700)
        self.assertFalse(result['numerically_converged']);self.assertEqual(result['local_iteration'],100)
    def test_duplicate_iterations(self):
        h=history();h[-1]['native_iteration']=700
        with self.assertRaises(ValueError):review.evaluate(h)
    def test_mixed_flows(self):
        h=history();h[-1]['single_arm_flow_m3h']=80
        with self.assertRaises(ValueError):review.evaluate(h)
    def test_nonfinite_residual(self):
        h=history();h[-1]['residuals'][0]=float('nan')
        with self.assertRaises(ValueError):review.evaluate(h)
    def test_missing_equations(self):
        h=history();h[-1]['residuals']=[1e-6]*5
        with self.assertRaises(ValueError):review.evaluate(h)
    def test_bad_limits(self):
        with self.assertRaises(ValueError):review.evaluate(history(),residual_limit=float('nan'))
class CurveTests(unittest.TestCase):
    def test_unit_conversion(self):self.assertAlmostEqual(table.normalize(points(),'provisional')[-1]['flow_m3s'],50/3600)
    def test_flow_sorting(self):self.assertEqual(table.normalize(list(reversed(points())),'provisional')[0]['flow_m3h'],10)
    def test_pressure_preserved(self):self.assertEqual(table.normalize(points(),'provisional')[-1]['static_delta_p_Pa'],1000)
    def test_duplicate_flow(self):
        p=points()
        with self.assertRaises(ValueError):table.normalize([p[0],p[0]],'provisional')
    def test_conflicting_units(self):
        p=points();p[0]['flow_m3s']=10
        with self.assertRaises(ValueError):table.normalize(p,'provisional')
    def test_nonfinite_pressure(self):
        p=points();p[0]['static_delta_p_Pa']=float('inf')
        with self.assertRaises(ValueError):table.normalize(p,'provisional')
    def test_unconverged_not_final(self):
        with self.assertRaises(ValueError):table.normalize(points(),'final')
    def test_final_is_metadata_only(self):
        p=points()
        for row in p:row.update(converged=True,native_reopen_verified=True)
        self.assertEqual(table.normalize(p,'final')[0]['status'],'FINAL_METADATA_CHECKED')
class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='fluent-skill-synthetic-')
        self.directory=Path(self.temporary.name).resolve()
        assert self.directory.is_relative_to(Path(tempfile.gettempdir()).resolve())
    def tearDown(self):self.temporary.cleanup()
    def run_script(self,name,*args):
        return subprocess.run([sys.executable,str(ROOT/'scripts'/name),*map(str,args)],capture_output=True,text=True)
    def source(self,name,payload):
        path=self.directory/name;path.write_text(json.dumps(payload),encoding='utf-8');return path
    def test_csv_contains_only_actual_points(self):
        source=self.source('points.json',points());out=self.directory/'points.csv'
        result=self.run_script('curve_table.py',source,'--mode','provisional','--output',out)
        self.assertEqual(result.returncode,0,result.stderr)
        with out.open(encoding='utf-8-sig') as file:rows=list(csv.DictReader(file))
        self.assertEqual(len(rows),3);self.assertAlmostEqual(float(rows[-1]['flow_m3s']),50/3600)
    def test_source_cannot_be_overwritten(self):
        source=self.source('points.json',points());original=source.read_bytes()
        result=self.run_script('curve_table.py',source,'--mode','provisional','--output',source,'--force')
        self.assertEqual(result.returncode,1);self.assertEqual(source.read_bytes(),original)
    def test_existing_export_needs_force(self):
        source=self.source('points.json',points());out=self.directory/'points.csv';out.write_text('preserve',encoding='utf-8')
        result=self.run_script('curve_table.py',source,'--mode','provisional','--output',out)
        self.assertEqual(result.returncode,1);self.assertEqual(out.read_text(),'preserve')
    @unittest.skipUnless(importlib.util.find_spec('scipy') and importlib.util.find_spec('numpy'),'optional NumPy/SciPy not installed')
    def test_interpolation_separate_from_cfd(self):
        source=self.source('points.json',points());out=self.directory/'points.csv';smooth=self.directory/'display.json'
        result=self.run_script('curve_table.py',source,'--mode','provisional','--output',out,'--smooth-output',smooth)
        self.assertEqual(result.returncode,0,result.stderr);data=json.loads(smooth.read_text())
        self.assertEqual(data['kind'],'DISPLAY_INTERPOLATION_NOT_CFD_POINTS');self.assertEqual(data['actual_point_count'],3)
        self.assertEqual(data['samples'][0]['flow_m3h'],10);self.assertEqual(data['samples'][-1]['flow_m3h'],50)
        values=[r['display_static_delta_p_Pa'] for r in data['samples']]
        self.assertTrue(all(a>=b for a,b in zip(values,values[1:])));self.assertLessEqual(max(values),1200)
    @unittest.skipUnless(importlib.util.find_spec('h5py'),'optional h5py not installed')
    def test_native_schema_iteration_and_strict_boundary(self):
        import h5py
        h=history();h[-1]['residuals']=[.00009999]*6
        source=self.source('history.json',h);native=self.directory/'synthetic.dat.h5'
        with h5py.File(native,'w') as file:
            for equation in review.EQUATIONS:
                group=file.require_group('results/residuals/phase-1/'+equation)
                group.create_dataset('iterations',data=[800]);group.create_dataset('data',data=[[.00010001,1.]])
        result=self.run_script('evaluate_convergence.py',source,'--native-data',native,'--require-converged')
        self.assertEqual(result.returncode,2,result.stderr);data=json.loads(result.stdout)
        self.assertTrue(data['native_data_matches']);self.assertFalse(data['gates']['native_all_six_residuals'])
    @unittest.skipUnless(importlib.util.find_spec('h5py'),'optional h5py not installed')
    def test_native_stale_iteration(self):
        import h5py
        source=self.source('history.json',history());native=self.directory/'stale.dat.h5'
        with h5py.File(native,'w') as file:
            for equation in review.EQUATIONS:
                group=file.require_group('results/residuals/phase-1/'+equation)
                group.create_dataset('iterations',data=[700]);group.create_dataset('data',data=[[1e-5,1.]])
        result=self.run_script('evaluate_convergence.py',source,'--native-data',native,'--require-converged')
        self.assertEqual(result.returncode,2,result.stderr);self.assertFalse(json.loads(result.stdout)['native_data_matches'])
if __name__=='__main__':unittest.main()
