import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src.aligned_client import GoClient, RequestFailure, MODEL
from src.aligned_pipeline import Pipeline
from src.aligned_protocol import parse_code, persist, instrumentation_guard, sha256, load_public


def problem():
    prompt='def f(x):\n    """Return x + 1."""\n'
    return {'dataset':'humaneval','problem_id':'HumanEval/999','generation_group_id':'test/f',
            'prompt':prompt,'prompt_hash':sha256(prompt),'entry_point':'f',
            'debug_tests':[{'kind':'python_assert','source_code':'assert f(1) == 2'}]}


class FakeTransport:
    def __init__(self):self.requests=[]
    def __call__(self,request,deadline):
        payload=json.loads(request.data)
        self.requests.append(payload)
        system=payload['messages'][0]['content']
        if system.startswith('Design exactly'):
            content=json.dumps([{'args':[i],'kwargs':{},'expected':i+1} for i in range(10)])
        elif system.startswith('Assess correctness'):
            content=json.dumps({'decision':'repair','explanation':'Return x+1.'})
        elif system.startswith('Analyze'):
            content='Return x + 1.'
        elif system.startswith('Instrument'):
            content='def f(x):\n    print(x)\n    return 0'
        elif 'Previous complete implementation' in payload['messages'][1]['content']:
            content='def f(x):\n    return x + 1'
        else:content='def f(x):\n    return 0'
        return 200, {'model':MODEL,'choices':[{'finish_reason':'stop','message':{'content':content}}],
                     'usage':{'prompt_tokens':7,'completion_tokens':9,'total_tokens':16}}


def debug(problem,code,**kwargs):
    passed='return x + 1' in code
    return {'visibility':'debug','status':'passed' if passed else 'failed', 'passed':passed,
            'checks_run':1,'calls_run':1,'observations':[{'args':'[1]','actual':'2' if passed else '0'}],
            'failures':[] if passed else [{'case':0,'error_type':'AssertionError'}],
            'trace':'x=1' if kwargs.get('trace') or kwargs.get('capture') else ''}


def feedback(result,mode):
    if result['visibility']!='debug':raise ValueError('hidden')
    return json.dumps({'trace':result['trace']} if mode=='trace' else {'passed':result['passed']})


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)
        self.key=patch.dict(os.environ,{'OPENCODE_API':'unit-test-not-a-real-key'})
        self.key.start()
    def tearDown(self):self.key.stop();self.temp.cleanup()
    def pipeline(self,sub='run',two_step=True):
        transport=FakeTransport()
        client=GoClient(self.directory/'unused',transport=transport)
        runner=Pipeline(self.directory/sub,client=client,debug=debug,format_feedback=feedback,
                        two_step=two_step,freeze_sha256='unit-freeze')
        return runner,transport
    def test_strict_parser_preserves_main(self):
        source='def f(x):\n    return x\nif __name__ == "__main__":\n    print(f(1))'
        self.assertEqual(parse_code(source,'f'),source)
        for source in ('text\n```python\ndef f(x): pass\n```','def g(x): pass',
                       'async def f(x): return x','```python\ndef f(x): pass\n```\n```python\npass\n```'):
            with self.assertRaises((ValueError,SyntaxError)):parse_code(source,'f')
    def test_instrumentation_guard(self):
        instrumentation_guard('def f(x):\n    return x','def f(x):\n    print(x)\n    return x')
        for code in ('def f(x):\n    return 0','def f(x):\n    print(open("hidden"))\n    return x',
                     'def f(x):\n    x+=1\n    return x'):
            with self.assertRaises(ValueError):instrumentation_guard('def f(x):\n    return x',code)
    def test_exclusive_ledger(self):
        path=self.directory/'exclusive.json'
        persist(path,{'one':1})
        with self.assertRaises(FileExistsError):persist(path,{'two':2})
        self.assertEqual(json.loads(path.read_text()),{'one':1})
    def test_pending_attempt_never_resends(self):
        transport=FakeTransport(); client=GoClient(self.directory,transport=transport)
        persist(self.directory/'initial/attempt.json',{'status':'pending'})
        with self.assertRaises(FileExistsError):client.call('initial',[],'session')
        self.assertEqual(transport.requests,[])
    def test_client_resume_and_configuration(self):
        transport=FakeTransport();client=GoClient(self.directory,transport=transport)
        messages=[{'role':'system','content':'test'},{'role':'user','content':'test'}]
        first=client.call('a',messages,'session');second=client.call('a',messages,'session')
        self.assertEqual(first,second);self.assertEqual(len(transport.requests),1)
        self.assertEqual(transport.requests[0]['max_tokens'],2048)
        with self.assertRaises(RequestFailure):client.call('a',messages+[messages[0]],'session')
    def test_wrong_model_or_truncation_not_source(self):
        for field,value in [('model','wrong'),('finish_reason','length')]:
            def transport(request,deadline):
                body={'model':MODEL,'choices':[{'finish_reason':'stop','message':{'content':'def f(x): pass'}}]}
                if field=='model':body['model']=value
                else:body['choices'][0][field]=value
                return 200,body
            client=GoClient(self.directory/field,transport=transport)
            with self.assertRaises(RequestFailure):client.call('a',[],'session')
            self.assertNotIn('content',json.loads((self.directory/field/'a/result.json').read_text()))
    def test_all_methods_share_initial_and_suite(self):
        runner,transport=self.pipeline()
        methods=['E0','E1','E2','E3','E4','E5','E6','TC_public']
        record=runner.run(problem(),methods)
        hashes={m['rounds'][0]['code_sha256'] for m in record['methods'].values()}
        self.assertEqual(len(hashes),1)
        self.assertEqual(sum(r['messages'][0]['content'].startswith('Design exactly') for r in transport.requests),1)
        self.assertEqual(record['methods']['E0']['stop_reason'],'no_debug')
        self.assertEqual(record['methods']['TC_public']['rounds'][0]['instrumentation']['accepted'],True)
        self.assertEqual(record['methods']['E6']['stop_reason'],'max_iterations')
    def test_two_step_changes_calls(self):
        first,a=self.pipeline('two');second,b=self.pipeline('one',two_step=False)
        first.run(problem(),['TC_public']);second.run(problem(),['TC_public'])
        self.assertEqual(len(a.requests)-len(b.requests),1)
        self.assertFalse(any(p['messages'][0]['content'].startswith('Analyze') for p in b.requests))
    def test_hidden_invariance(self):
        requests=[];trajectories=[]
        for i in range(2):
            persist(self.directory/'dataset/evaluator/hidden.json',{'hidden_canary':str(i)},exclusive=False)
            runner,transport=self.pipeline(str(i))
            record=runner.run(problem(),['E4','TC_public'])
            requests.append(transport.requests);trajectories.append(record['methods'])
        self.assertEqual(requests[0],requests[1]);self.assertEqual(trajectories[0],trajectories[1])
        self.assertNotIn('hidden_canary',json.dumps(requests))
    def test_resume_scope_changes_block(self):
        runner,transport=self.pipeline();runner.run(problem(),['E0'])
        before=len(transport.requests);runner.run(problem(),['E0'])
        self.assertEqual(before,len(transport.requests))
        with self.assertRaises(ValueError):runner.run(problem(),['E0','E4'])
    def test_unsupported_adapter_no_api(self):
        runner,transport=self.pipeline();p=problem();p['dataset']='classeval'
        self.assertEqual(runner.run(p,['E0'])['status'],'adapter_blocked')
        self.assertEqual(transport.requests,[])
    def test_public_loader_does_not_read_hidden(self):
        base=self.directory/'dataset/processed/humaneval/generator'
        base.mkdir(parents=True)
        p=problem();row={k:v for k,v in p.items() if k not in {'dataset','debug_tests'}}
        (base/'problems.jsonl').write_text(json.dumps(row)+'\n')
        (base/'public_tests.jsonl').write_text(json.dumps({'problem_id':p['problem_id'],'tests':p['debug_tests']})+'\n')
        hidden=base.parent/'evaluator';hidden.mkdir()
        (hidden/'hidden_tests.jsonl').write_text('THIS IS NOT JSON and must never be parsed')
        self.assertEqual(load_public(self.directory,'humaneval')[0]['debug_tests'],p['debug_tests'])


if __name__=='__main__':unittest.main()
