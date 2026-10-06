"""ClassEval-only binding; old dataset registries/pipelines stay unchanged."""
import types
from .typed_self_test_profile_official_v4 import TypedPipeline


class ClassPipeline(TypedPipeline):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        function=self.run.__func__
        context=dict(function.__globals__,SUPPORTED=set(function.__globals__['SUPPORTED'])|{'classeval'})
        private=types.FunctionType(function.__code__,context,'class_run',function.__defaults__,function.__closure__)
        private.__kwdefaults__=function.__kwdefaults__
        self.run=types.MethodType(private,self)
