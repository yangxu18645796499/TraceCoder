"""No API: binding rejects unrelated arms and does not mutate old registries."""
import tempfile
import unittest
from pathlib import Path
from src.typed_self_test_profile_bcb_v45 import BCBPipeline
from src import aligned_pipeline


class StubClient:
    max_tokens=repair_max_tokens=16384
    timeout=180
    reasoning_effort='low'
    def policy(self):return {'test_fixture':True}
    def for_directory(self,path):
        Path(path).mkdir(parents=True,exist_ok=True);return self
    def call(self,identity,messages,session):
        if identity!='initial':raise AssertionError('No unexpected model phase')
        return {'content':'def task_func():\n return 42\n'}


class BCBProfileTests(unittest.TestCase):
    def test_only_two_arms_and_shared_initial_without_registry_mutation(self):
        old=set(aligned_pipeline.SUPPORTED)
        with tempfile.TemporaryDirectory() as folder:
            pipeline=BCBPipeline(Path(folder),client=StubClient(),debug=lambda *a,**k:{'passed':True,'status':'passed'},
                format_feedback=lambda *a:'public',decode_document=lambda x:x,decoder_sha256='test-only')
            problem={'dataset':'bigcodebench_complete','problem_id':'BigCodeBench/test','entry_point':'task_func',
                'generation_group_id':'bcb:test','prompt_hash':'test-prompt','prompt':'Return 42','debug_tests':[{'test_fixture':True}]}
            result=pipeline.run(problem,['E0','TC_public'])
            self.assertEqual(set(result['methods']),{'E0','TC_public'},result)
            self.assertEqual(result['methods']['E0']['final_code'],result['methods']['TC_public']['rounds'][0]['code'])
            with self.assertRaises(ValueError):pipeline.run(problem,['E1'])
            with self.assertRaises(ValueError):pipeline.run(dict(problem,dataset='classeval'),['E0'])
        self.assertEqual(aligned_pipeline.SUPPORTED,old)


if __name__=='__main__':unittest.main()
