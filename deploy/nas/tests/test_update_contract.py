import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('nas_updater',ROOT/'updater.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
spec=importlib.util.spec_from_file_location('nas_verifier',ROOT/'verify_runtime.py')
verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)

OLD='''services:
  deeptutor:
    image: local/deeptutor:old
    container_name: deeptutor
    labels:
      deployment.upstream.digest: sha256:old
    ports:
      - "0.0.0.0:3782:3782"
    volumes:
      - ./data:/app/data
      - ./deploy/context-cache-v7/patched/agents/loop/agent_loop.py:/app/deeptutor/agents/loop/agent_loop.py:ro
    environment:
      DEEPTUTOR_AUTO_COMPACT_TOKENS: "500000"
    mem_limit: 2g
'''
IMAGE='sha256:'+'a'*64
DIGEST='sha256:'+'b'*64

class UpgradeContractTests(unittest.TestCase):
    def test_old_mounts_are_removed_but_data_and_limits_remain(self):
        value=updater.rewrite_compose(OLD,IMAGE,DIGEST)
        self.assertIn('image: '+IMAGE,value)
        self.assertNotIn('context-cache-v7',value)
        for text in ['./data:/app/data','mem_limit: 2g','"0.0.0.0:3782:3782"','"500000"']:
            self.assertIn(text,value)

    def test_trial_is_loopback_only(self):
        value=updater.rewrite_compose(OLD,IMAGE,DIGEST,True)
        self.assertIn('"127.0.0.1:3782:3782"',value)
        self.assertNotIn('"0.0.0.0:3782:3782"',value)

    def test_next_upgrade_accepts_no_legacy_mounts(self):
        first=updater.rewrite_compose(OLD,IMAGE,DIGEST)
        self.assertEqual(updater.rewrite_compose(first,IMAGE,DIGEST),first)

    def test_unknown_source_mount_is_not_silently_removed(self):
        value=OLD.replace('agents/loop/agent_loop.py','unknown/private.py')
        with self.assertRaises(ValueError):updater.rewrite_compose(value,IMAGE,DIGEST)

    def test_missing_policy_is_not_accepted(self):
        with self.assertRaises(ValueError):updater.rewrite_compose(OLD.replace('500000','100000'),IMAGE,DIGEST)

    def test_invalid_image_identity_refuses(self):
        with self.assertRaises(ValueError):updater.rewrite_compose(OLD,'latest',DIGEST)

    def test_malformed_tar_does_not_escape(self):
        for name,kind in [('../outside',tarfile.REGTYPE),('/etc/secret',tarfile.REGTYPE),('deeptutor/link',tarfile.SYMTYPE)]:
            raw=io.BytesIO()
            with tarfile.open(fileobj=raw,mode='w') as tar:
                info=tarfile.TarInfo(name);info.type=kind;info.linkname='/etc'
                tar.addfile(info)
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):updater.extract_source(raw.getvalue(),Path(folder)/'source')

    def test_data_verification_allows_only_new_metadata(self):
        before={'messages':{'1':{'content':'original','metadata':{'model_turn':'old-prefix'}}}}
        after=copy.deepcopy(before);after['messages']['1']['metadata']['new-source']='extra'
        verifier.assert_preserved(before,after)
        after['messages']['1']['metadata']['model_turn']='rewritten'
        with self.assertRaises(RuntimeError):verifier.assert_preserved(before,after)

    def test_answer_and_grade_changes_are_rejected(self):
        before={'learning':{'q1':{'answer':'original','score':0.7}}}
        for after in [{'learning':{'q1':{'answer':'edited','score':0.7}}},{'learning':{'q1':{'answer':'original','score':1}}}]:
            with self.assertRaises(RuntimeError):verifier.assert_preserved(before,after)

    def test_missing_history_is_rejected(self):
        with self.assertRaises(RuntimeError):verifier.assert_preserved({'messages':{'1':'keep'}},{'messages':{}})

    def test_unapproved_release_is_rejected(self):
        with self.assertRaises(ValueError):updater.validate_approval({'schema':1,'repository':updater.REPOSITORY})

if __name__=='__main__':unittest.main()
