import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import bulk_descriptions as bd
NOW = '2026-11-01T12:00:00Z'
DATA = {'custom_descriptions': {'x': 'old'}, 'custom_descriptions_en': {'x': ''}, 'orders': {'a': 1}}
QUOTES = {'x': {'name': 'Name', 'source': 'CTV', 'base_price_vnd': 5}}
class DescriptionTests(unittest.TestCase):
    def snap(self, data=None):
        return bd.catalog(DATA if data is None else data, QUOTES, NOW)
    def test_export_and_mutation(self):
        snap = self.snap()
        self.assertNotIn('orders', snap)
        plan = bd.build_plan(snap, [{'key':'x','expected_name':'Name','description_vi':None}], NOW)
        out = bd.mutate(DATA, plan)
        self.assertEqual(out['custom_descriptions'], {})
        self.assertEqual(out['custom_descriptions_en'], {'x':''})
        self.assertEqual(out['orders'], DATA['orders'])
    def test_validation(self):
        for change in [{'key':'no','description_vi':'a'}, {'key':'x','description_vi':True}, {'key':'x'}, {'key':'x','extra':1,'description_vi':'a'}, {'key':'x','expected_name':'wrong','description_vi':'a'}, {'key':'x','description_vi':'😀'*1001}]:
            with self.subTest(change=change), self.assertRaises(bd.PriceError):
                bd.build_plan(self.snap(), [change], NOW)
    def test_selected_language_drift_and_tampering(self):
        plan = bd.build_plan(self.snap(), [{'key':'x','description_vi':'new'}], NOW)
        data = copy.deepcopy(DATA); data['custom_descriptions_en']['x']='changed'
        bd.validate_plan(plan, self.snap(data), NOW)
        data['custom_descriptions']['x']='drift'
        with self.assertRaises(bd.PriceError): bd.validate_plan(plan, self.snap(data), NOW)
        tampered=copy.deepcopy(plan); tampered['changes'][0]['after']['description_vi']='tampered'
        with self.assertRaises(bd.PriceError): bd.validate_plan(tampered,self.snap(), NOW)
        with self.assertRaises(bd.PriceError): bd.validate_plan(plan,self.snap(),'2026-11-01T12:31:00Z')

class LifecycleTests(unittest.TestCase):
    def test_final_reread_decimal_backup_inverse_and_restart(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); db=root/'db.json'; config=root/'config.env'; backup=root/'backup'; receipt=root/'receipt'
            config.write_text('CTV_API_KEY=dummy\n')
            initial=copy.deepcopy(DATA); initial['unknown']=bd.bp.loads('0.123456789012345678901')
            db.write_bytes(bd.bp.json_bytes(initial))
            plan=bd.build_plan(bd.catalog(initial,QUOTES,NOW),[{'key':'x','description_vi':'new'}],NOW)
            class Service:
                running=True
                def verify_database(self, path): self.verified=True
                def stop(self):
                    self.running=False
                    latest=bd.bp.read_json(db); latest['orders']['new']=2
                    db.write_bytes(bd.bp.json_bytes(latest))
                def ensure_stopped(self): assert not self.running
                def start(self): self.running=True
                def ensure_running(self): assert self.running
            service=Service()
            with patch.object(bd.bp,'fetch_quotes',return_value=QUOTES):
                audit=bd.apply_plan(config,db,plan,execute=True,backup=backup,receipt=receipt,service=service,now=NOW)
            actual=bd.bp.read_json(db)
            self.assertEqual(actual['orders']['new'],2)
            self.assertEqual(actual['unknown'],initial['unknown'])
            self.assertEqual(audit['status'],'applied')
            self.assertEqual(audit['inverse_changes'],[{'key':'x','expected_name':'Name','description_vi':'old'}])
            self.assertEqual(bd.bp.read_json(backup)['orders']['new'],2)
            self.assertTrue(service.running)
    def test_write_failure_recovers_service(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); db=root/'db'; config=root/'config'
            config.write_text(''); db.write_bytes(bd.bp.json_bytes(DATA))
            plan=bd.build_plan(bd.catalog(DATA,QUOTES,NOW),[{'key':'x','description_vi':'new'}],NOW)
            class Service:
                started=False
                def verify_database(self,path): pass
                def stop(self): pass
                def ensure_stopped(self): pass
                def start(self): self.started=True
                def ensure_running(self): pass
            service=Service()
            with patch.object(bd.bp,'fetch_quotes',return_value=QUOTES), patch.object(bd.bp,'write_new',side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    bd.apply_plan(config,db,plan,execute=True,backup=root/'backup',receipt=root/'receipt',service=service,now=NOW)
            self.assertTrue(service.started)
            self.assertEqual(bd.bp.read_json(db),DATA)

class BoundaryTests(unittest.TestCase):
    def test_missing_empty_null_and_unicode(self):
        data=copy.deepcopy(DATA); del data['custom_descriptions_en']
        snap=bd.catalog(data,QUOTES,NOW)
        self.assertIsNone(snap['products'][0]['description_en'])
        with self.assertRaises(bd.PriceError): bd.build_plan(snap,[{'key':'x','description_en':''}],NOW)
        with self.assertRaises(bd.PriceError): bd.build_plan(snap,[{'key':'x','description_en':'   '}],NOW)
        bd.build_plan(snap,[{'key':'x','description_vi':'😀'*1000}],NOW)
        for value in ['\ud800','\x00']:
            with self.assertRaises(bd.PriceError): bd.build_plan(snap,[{'key':'x','description_vi':value}],NOW)
    def test_duplicate_json_keys_changes_and_identity(self):
        with self.assertRaises(bd.PriceError): bd.bp.loads('{"key":"x","key":"y"}')
        snap=bd.catalog(DATA,QUOTES,NOW)
        with self.assertRaises(bd.PriceError): bd.build_plan(snap,[{'key':'x','description_vi':'a'}]*2,NOW)
        plan=bd.build_plan(snap,[{'key':'x','description_vi':'a'}],NOW)
        for field,value in [('name','renamed'),('source','LOCAL')]:
            changed=copy.deepcopy(snap); changed['products'][0][field]=value
            with self.assertRaises(bd.PriceError): bd.validate_plan(plan,changed,NOW)
    def test_output_collision_and_preview_do_not_stop(self):
        import tempfile
        from unittest.mock import Mock, patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); db=root/'db'; config=root/'config'
            config.write_text(''); db.write_bytes(bd.bp.json_bytes(DATA))
            plan=bd.build_plan(bd.catalog(DATA,QUOTES,NOW),[{'key':'x','description_vi':'a'}],NOW)
            service=Mock()
            with patch.object(bd.bp,'fetch_quotes',return_value=QUOTES):
                result=bd.apply_plan(config,db,plan,service=service,now=NOW)
                self.assertEqual(result['status'],'preview'); service.stop.assert_not_called()
                with self.assertRaises(bd.PriceError):
                    bd.apply_plan(config,db,plan,execute=True,backup=db,receipt=root/'receipt',service=service,now=NOW)
                service.stop.assert_not_called()



class AdditionalSafetyTests(unittest.TestCase):
    def test_legacy_descriptions_export_and_unrelated_rows_do_not_block(self):
        data=copy.deepcopy(DATA); data['custom_descriptions']['legacy']='x'*2001
        data['custom_descriptions']['x']=''
        quotes=copy.deepcopy(QUOTES); quotes['legacy']={'name':'Legacy','source':'CTV'}
        snap=bd.catalog(data,quotes,NOW)
        plan=bd.build_plan(snap,[{'key':'x','description_vi':'new'}],NOW)
        bd.validate_plan(plan,snap,NOW)
        result=bd.mutate(data,plan)
        self.assertEqual(result['custom_descriptions']['legacy'],'x'*2001)
        self.assertEqual(result['custom_descriptions']['x'],'new')
    def test_stop_flush_description_drift_rejects_recovers_and_never_writes(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); db=root/'db'; config=root/'config'; config.write_text('')
            db.write_bytes(bd.bp.json_bytes(DATA))
            plan=bd.build_plan(bd.catalog(DATA,QUOTES,NOW),[{'key':'x','description_vi':'new'}],NOW)
            class Service:
                started=False
                def verify_database(self,path): pass
                def stop(self):
                    flushed=copy.deepcopy(DATA); flushed['custom_descriptions']['x']='concurrent'
                    db.write_bytes(bd.bp.json_bytes(flushed))
                def ensure_stopped(self): pass
                def start(self): self.started=True
                def ensure_running(self): pass
            service=Service()
            with patch.object(bd.bp,'fetch_quotes',return_value=QUOTES), patch.object(bd.bp,'write_new') as write:
                with self.assertRaises(bd.PriceError):
                    bd.apply_plan(config,db,plan,execute=True,backup=root/'backup',receipt=root/'receipt',service=service,now=NOW)
                write.assert_not_called()
            self.assertTrue(service.started)
            self.assertEqual(bd.bp.read_json(db)['custom_descriptions']['x'],'concurrent')
    def test_shared_price_apply_lock_blocks_before_service_stop(self):
        import tempfile
        from unittest.mock import Mock, patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); db=root/'db'; config=root/'config'; config.write_text('')
            db.write_bytes(bd.bp.json_bytes(DATA))
            plan=bd.build_plan(bd.catalog(DATA,QUOTES,NOW),[{'key':'x','description_vi':'new'}],NOW)
            service=Mock()
            with bd.bp.apply_lock(db), patch.object(bd.bp,'fetch_quotes',return_value=QUOTES):
                with self.assertRaises(bd.PriceError):
                    bd.apply_plan(config,db,plan,execute=True,backup=root/'backup',receipt=root/'receipt',service=service,now=NOW)
            service.stop.assert_not_called()



    def test_restart_failure_leaves_applied_receipt_and_never_restores_backup(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); db=root/'db'; config=root/'config'; config.write_text('')
            db.write_bytes(bd.bp.json_bytes(DATA))
            plan=bd.build_plan(bd.catalog(DATA,QUOTES,NOW),[{'key':'x','description_vi':'new'}],NOW)
            class Service:
                def verify_database(self,path): pass
                def stop(self): pass
                def ensure_stopped(self): pass
                def start(self): raise bd.PriceError('Service command failed')
                def ensure_running(self): pass
            with patch.object(bd.bp,'fetch_quotes',return_value=QUOTES):
                with self.assertRaises(bd.PriceError):
                    bd.apply_plan(config,db,plan,execute=True,backup=root/'backup',receipt=root/'receipt',service=Service(),now=NOW)
            self.assertEqual(bd.bp.read_json(db)['custom_descriptions']['x'],'new')
            self.assertEqual(bd.bp.read_json(root/'receipt')['status'],'applied')
            self.assertEqual(bd.bp.read_json(root/'backup'),DATA)

if __name__ == '__main__':
    unittest.main()
