"""Free audit of original algorithm and the minimal public adapter."""
import ast
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
UPSTREAM='cbd536eb5a02b53ee835cae2f07f5b7ed8bb694d'


class OriginalBoundaryTests(unittest.TestCase):
    def original(self,name):
        return subprocess.check_output(['git','show',UPSTREAM+':'+name],cwd=ROOT).decode('utf-8')

    def test_original_method_and_prompts_unchanged(self):
        for name in ('problem_processor.py','config.py','reporting.py','src/postprocessing.py','src/traceRunner.py'):
            with self.subTest(name=name):
                self.assertEqual(ast.dump(ast.parse(self.original(name))),
                                 ast.dump(ast.parse((ROOT/name).read_text('utf-8'))))

    def test_original_extractors_unchanged(self):
        def functions(text):return {n.name:ast.dump(n) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
        before=functions(self.original('src/generation.py'))
        after=functions((ROOT/'src/generation.py').read_text('utf-8'))
        for name in ('get_split_point_after_last_return','remove_content_after_last_return','extract_python_code_with_logic'):
            self.assertEqual(before[name],after[name])

    def test_adapter_rejects_hidden_fields(self):
        from public_humaneval import process_public_problem
        public={'task_id':'HumanEval/0','entry_point':'f','prompt':'p','complete_prompt':'p','test':'public'}
        for field in ('hidden_tests','reference_solution','canonical_solution'):
            with self.subTest(field=field),self.assertRaises(ValueError):
                process_public_problem(dict(public,**{field:'forbidden'}),None,None,None)

    def test_adapter_routes_public_only_original_processor(self):
        from public_humaneval import process_public_problem
        from src.generation import configure_transport
        public={'task_id':'HumanEval/0','entry_point':'f','prompt':'p','complete_prompt':'p','test':'public'}
        calls=[]
        def process(problem,task,args,checker):
            calls.append((problem,task,args,checker));return {'done':True}
        module=types.SimpleNamespace(process_problem=process)
        checker=lambda *args:True
        try:
            with patch.dict(sys.modules,{'problem_processor':module}):
                self.assertEqual(process_public_problem(public,lambda *a,**k:None,checker,lambda *a:None),{'done':True})
            problem,task,args,selected_checker=calls[0]
            self.assertEqual(problem,public)
            self.assertEqual((args.max_debug_attempts,args.max_no_improvement_streak),(5,2))
            self.assertEqual(args.model,'deepseek-v3.2')
            self.assertIs(selected_checker,checker)
        finally:configure_transport(None)

    def test_legacy_entry_is_guarded(self):
        source=(ROOT/'trace_learn_coder.py').read_text('utf-8')
        tree=ast.parse(source)
        self.assertIsInstance(tree.body[-1],ast.If)
        self.assertIsInstance(tree.body[-1].body[0],ast.Raise)
        self.assertIn('scripts/he/he_campaign.py',source)


if __name__=='__main__':unittest.main()
