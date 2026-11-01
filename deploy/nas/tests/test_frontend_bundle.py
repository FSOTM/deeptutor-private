"""Verify frontend release identity and safe artifact extraction."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('frontend_bundle',Path(__file__).resolve().parents[1]/'frontend_bundle.py')
bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)
COMMIT='a'*40

class FrontendBundleTests(unittest.TestCase):
    def make(self,root,extra=None):
        archive=root/'frontend.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for name,kind in [('server.js',tarfile.REGTYPE),('.next/static',tarfile.DIRTYPE)]+([extra] if extra else []):
                member=tarfile.TarInfo(name);member.type=kind
                if member.isfile():member.size=1;tar.addfile(member,io.BytesIO(b'x'))
                else:member.linkname='/etc';tar.addfile(member)
        return archive,{'source_commit':COMMIT,'url':'https://github.com/FSOTM/deeptutor-private/releases/download/nas-'+COMMIT+'/frontend.tar.gz','sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size}

    def test_verified_standalone_runtime_extracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive,metadata=self.make(root)
            bundle.unpack(archive,root/'web-runtime',metadata,COMMIT)
            self.assertTrue((root/'web-runtime/server.js').is_file())

    def test_changed_download_refuses_before_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive,metadata=self.make(root);archive.write_bytes(b'tampered')
            with self.assertRaises(ValueError):bundle.unpack(archive,root/'web-runtime',metadata,COMMIT)
            self.assertFalse((root/'web-runtime').exists())

    def test_wrong_release_identity_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive,metadata=self.make(root)
            metadata['url']=metadata['url'].replace(COMMIT,'b'*40)
            with self.assertRaises(ValueError):bundle.unpack(archive,root/'web-runtime',metadata,COMMIT)

    def test_paths_symlinks_and_environment_files_refuse(self):
        for extra in [('../escaped',tarfile.REGTYPE),('linked',tarfile.SYMTYPE),('.env.local',tarfile.REGTYPE)]:
            with self.subTest(extra=extra),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);archive,metadata=self.make(root,extra)
                with self.assertRaises(ValueError):bundle.unpack(archive,root/'web-runtime',metadata,COMMIT)
                self.assertFalse((root/'web-runtime').exists())

if __name__=='__main__':unittest.main()
