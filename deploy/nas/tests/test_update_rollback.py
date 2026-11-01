"""Exercise real backup/restore filesystem operations with a fake Docker boundary."""
import importlib.util
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from test_update_contract import OLD, IMAGE, DIGEST

spec=importlib.util.spec_from_file_location('rollback_updater',Path(__file__).resolve().parents[1]/'updater.py')
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)


class RollbackTests(unittest.TestCase):
    def exercise(self,failure=None):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'deeptutor';root.mkdir()
        (root/'compose.yaml').write_text(OLD)
        lock=root/'data/user/.runtime/workspace-activity.sqlite3';lock.parent.mkdir(parents=True)
        with sqlite3.connect(lock) as db:db.execute('PRAGMA user_version=1')
        answer=root/'data/user/answer.txt';answer.write_text('original learner answer; score=0.7')
        stage=root/'.updates/stage';stage.mkdir(parents=True)
        app=u.Updater(root)
        state={'image':'old-image','running':True,'calls':[],'gate':False}
        approval={'source_commit':'1'*40,'upstream_manifest_digest':DIGEST,'upstream_version':'1.7.1'}
        def inspect(name):
            self.assertEqual(name,'deeptutor')
            return {'Id':'original-container','Image':state['image'],'State':{'Running':state['running']}}
        def command(args,**kwargs):
            state['calls'].append([str(a) for a in args])
            if args[:2]==[u.DOCKER,'stop']:state['running']=False
            if args[:3]==[u.DOCKER,'exec','-u'] and failure=='after_migration':
                raise RuntimeError('simulated post-migration validation failure')
        def candidate(folder,image,baseline):
            if failure=='candidate':raise RuntimeError('simulated isolated candidate failure')
            baseline.write_text('{}')
        def recreate():
            text=(root/'compose.yaml').read_text()
            state['image']=IMAGE if IMAGE in text else 'old-image'
            state['running']=True
            if state['image']==IMAGE:answer.write_text('changed by new-version migration')
        def copy(source,target):shutil.copytree(source,target)
        with patch.object(u,'inspect',side_effect=inspect),patch.object(u,'run',side_effect=command),\
             patch.object(u,'copy_data',side_effect=copy),patch.object(u,'container_inventory',return_value={'/unrelated':('id','running','original-start')}),\
             patch.object(u.shutil,'disk_usage',return_value=SimpleNamespace(free=100*1024**3)),\
             patch.object(app,'idle'),patch.object(app,'candidate',side_effect=candidate),\
             patch.object(app,'recreate',side_effect=recreate),patch.object(app,'reload_tls'),\
             patch.object(app,'gate',side_effect=lambda value:state.update(gate=value)):
            if failure:
                with self.assertRaises(RuntimeError):app.apply(approval,stage,IMAGE)
            else:app.apply(approval,stage,IMAGE)
        return root,answer,state

    def test_candidate_failure_keeps_original_answer_and_compose(self):
        root,answer,state=self.exercise('candidate')
        self.assertEqual(answer.read_text(),'original learner answer; score=0.7')
        self.assertEqual((root/'compose.yaml').read_text(),OLD)
        self.assertEqual(state['image'],'old-image')
        self.assertTrue(state['running']);self.assertFalse(state['gate'])

    def test_failed_migration_restores_cold_data_without_discarding_failed_copy(self):
        root,answer,state=self.exercise('after_migration')
        self.assertEqual(answer.read_text(),'original learner answer; score=0.7')
        self.assertEqual((root/'compose.yaml').read_text(),OLD)
        failed=list((root/'.updates/backups').glob('*/failed-data/user/answer.txt'))
        self.assertEqual(len(failed),1)
        self.assertEqual(failed[0].read_text(),'changed by new-version migration')
        self.assertEqual(state['image'],'old-image')
        self.assertTrue(state['running']);self.assertFalse(state['gate'])

    def test_success_keeps_cold_rollback_copy_and_reopens_entry(self):
        root,answer,state=self.exercise()
        saved=list((root/'.updates/backups').glob('*/data/user/answer.txt'))
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0].read_text(),'original learner answer; score=0.7')
        self.assertEqual(state['image'],IMAGE)
        self.assertIn('"0.0.0.0:3782:3782"',(root/'compose.yaml').read_text())
        self.assertFalse(state['gate'])


if __name__=='__main__':unittest.main()
