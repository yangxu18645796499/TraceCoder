"""LCB scheduler binding; no mutation of old/global dataset registries."""
import types
from .typed_self_test_profile_official_v4 import TypedPipeline
from .aligned_protocol import parse_code

class LCBPipeline(TypedPipeline):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        function = self.run.__func__
        context = dict(function.__globals__, SUPPORTED=set(function.__globals__['SUPPORTED']) | {'livecodebench_450'})
        private = types.FunctionType(function.__code__, context, 'lcb_run', function.__defaults__, function.__closure__)
        private.__kwdefaults__ = function.__kwdefaults__
        self.run = types.MethodType(private, self)

    def _code(self, content, problem):
        return parse_code(content, problem.get('entry_point'))
