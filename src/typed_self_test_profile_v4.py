"""Explicit opt-in typed self-tests, not adopted by the active v3 experiment.

Reuse the exact repair scheduler without modifying its process-global parser.
Keep wire JSON on disk and decode only at the public-debug boundary. A future
freeze must pin this module, the scheduler and the caller's data-only decoder.
"""
import json
import types
from .aligned_pipeline import Pipeline

VERSION = 'typed-self-tests-v4-prototype'
OLD_SUFFIX = '\nEach case must have exactly args (JSON array), kwargs (JSON object), and expected (JSON value). Use ten distinct valid inputs.'
NEW_SUFFIX = '''
Return a JSON object with exactly schema_version and cases.
schema_version must be "typed-self-tests-v4-prototype".
cases must contain exactly ten distinct valid inputs, each with exactly args
(array of positional arguments), kwargs (object of keyword arguments), expected.
Use ordinary JSON for None/bool/int/finite-float/string/list/string-key dict.
Preserve Python types with these data-only tags when necessary:
tuple: {"$tuple": [items]}; set: {"$set": [items], "frozen": false};
frozenset: {"$set": [items], "frozen": true}; complex: {"$complex": [real, imag]};
dict with non-string keys or reserved $ keys: {"$dict": [[key, value], ...]};
bytes: {"$bytes": "base64"}. Tags may be nested. Do not emit NaN, code or pickle.
Do not convert tuples to lists or numeric dictionary keys to strings.
'''


class TypedClient:
    def __init__(self, wrapped, decoder_sha256):
        self.wrapped, self.decoder_sha256 = wrapped, decoder_sha256

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def policy(self):
        return dict(self.wrapped.policy(), self_test_protocol=VERSION,
                    self_test_decoder_sha256=self.decoder_sha256,
                    wire_storage=True, old_response_reinterpretation=False)

    def for_directory(self, directory):
        return type(self)(self.wrapped.for_directory(directory), self.decoder_sha256)

    def call(self, call_id, messages, session):
        if call_id == 'generated_tests':
            if len(messages) != 2 or not messages[1]['content'].endswith(OLD_SUFFIX):
                raise ValueError('Scheduler test-template changed; new freeze required')
            messages = [
                {'role': 'system', 'content': 'Design exactly ten typed tests from the specification without seeing an implementation. Return only the versioned JSON object, no Python code or markdown.'},
                {'role': 'user', 'content': messages[1]['content'][:-len(OLD_SUFFIX)] + NEW_SUFFIX},
            ]
        return self.wrapped.call(call_id, messages, session)


class TypedPipeline(Pipeline):
    def __init__(self, directory, *, client, debug, format_feedback, decode_document,
                 decoder_sha256, limits=None, two_step=True, freeze_sha256=None):
        def wire_parser(content, count=10):
            # Validation creates only fixed data records; decoded tuples/sets
            # must NOT enter JSON artifact storage.
            decode_document(content, count=count)
            document = json.loads(content)
            return document['cases']

        def typed_debug(*args, **kwargs):
            cases = kwargs.get('cases')
            if cases is not None:
                document = {'schema_version': VERSION, 'cases': cases}
                kwargs['cases'] = decode_document(json.dumps(document, allow_nan=False), count=len(cases))
            return debug(*args, **kwargs)

        super().__init__(directory, client=TypedClient(client, decoder_sha256), debug=typed_debug,
                         format_feedback=format_feedback, limits=limits, two_step=two_step,
                         freeze_sha256=freeze_sha256)
        # A private globals map avoids changing ordinary Pipeline or another
        # process/thread's protocol. No candidate/model source is compiled here.
        globals_map = dict(Pipeline.run.__globals__, parse_generated_tests=wire_parser)
        function = types.FunctionType(Pipeline.run.__code__, globals_map, 'typed_run', Pipeline.run.__defaults__, Pipeline.run.__closure__)
        function.__kwdefaults__ = Pipeline.run.__kwdefaults__
        self.run = types.MethodType(function, self)
