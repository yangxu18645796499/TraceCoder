"""Prospective BCB-only, two-arm binding; legacy/frozen registries untouched."""
import types
from .typed_self_test_profile_official_v4 import TypedPipeline


class BCBPipeline(TypedPipeline):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        function=self.run.__func__
        context=dict(function.__globals__,SUPPORTED=set(function.__globals__['SUPPORTED'])|{'bigcodebench_complete'})
        bound=types.FunctionType(function.__code__,context,'bcb_run',function.__defaults__,function.__closure__)
        bound.__kwdefaults__=function.__kwdefaults__
        self._bcb_run=types.MethodType(bound,self)
        # TypedPipeline installs an instance-level run; restore this guarded
        # wrapper explicitly instead of leaving the base binding in place.
        self.run=types.MethodType(BCBPipeline.run,self)

    def run(self,problem,methods):
        if problem.get('dataset')!='bigcodebench_complete' or set(methods)-{'E0','TC_public'}:
            raise ValueError('Only BCB E0/TraceCoder authorized for this binding')
        return self._bcb_run(problem,methods)
