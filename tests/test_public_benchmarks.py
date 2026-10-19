import unittest
from unittest.mock import patch
from public_benchmarks import extract_stdin_code,process_public_benchmark
from src.postprocessing import extract_python_code


class DatasetAdapterTests(unittest.TestCase):
    def test_lcb_stdin_keeps_main_and_script(self):
        text='```python\nimport sys\nif __name__ == "__main__":\n print(sys.stdin.read())\n```'
        self.assertIn('__main__',extract_stdin_code(text))
        self.assertNotIn('```',extract_stdin_code(text))

    def test_he_original_extractor_unchanged(self):
        text='```python\ndef f(): return 1\nif __name__ == "__main__":\n print(f())\n```'
        self.assertNotIn('__main__',extract_python_code(text))
        self.assertIn('__main__',extract_stdin_code(text))

    def test_hidden_fields_rejected(self):
        with self.assertRaises(ValueError):process_public_benchmark({'hidden':[]},None,None,None,dataset='mbpp_500')

    def test_upstream_policy_unchanged(self):
        public=dict(task_id='MBPP/11',entry_point='f',prompt='public',complete_prompt='public',test='assert f()==1')
        with patch('problem_processor.process_problem',return_value={}) as called:
            process_public_benchmark(public,lambda *a:None,lambda *a:None,lambda *a:None,dataset='mbpp_500')
            args=called.call_args.args[2]
            self.assertEqual((args.max_debug_attempts,args.max_no_improvement_streak),(5,2))
            self.assertFalse(args.no_instrumentation)


if __name__=='__main__':unittest.main()
