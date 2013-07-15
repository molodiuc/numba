import inspect
import llvm.core as lc
from numbapro.npm import symbolic, typing, codegen
from numbapro.npm.compiler import get_func_name
from numbapro.cudadrv import nvvm, driver
from .execution import CUDAKernel
from .typing import cudapy_global_typing_ext, cudapy_call_typing_ext
from .codegen import cudapy_global_codegen_ext, cudapy_call_codegen_ext
from .passes import bind_scalar_constants

CUDA_ADDR_SIZE = tuple.__itemsize__ * 8     # matches host

def compile_kernel(func, argtys):
    lmod, lfunc = compile_common(func, None, argtys)
    # PTX-ization
    cudakernel = CUDAKernel(lfunc.name, to_ptx(lfunc), argtys)
    #print cudakernel.ptx
    return cudakernel

def compile_device(func, retty, argtys, inline=False):
    lmod, lfunc = compile_common(func, retty, argtys)
    if inline:
        lfunc.add_attribute(lc.ATTR_ALWAYS_INLINE)
    return DeviceFunction(func, lmod, lfunc, retty, argtys)

def declare_device_function(name, retty, argtys):
    lmod = lc.Module.new('extern-%s' % name)
    ts = codegen.TypeSetter(intp=CUDA_ADDR_SIZE)
    lret = ts.to_llvm(retty)
    largs = [ts.to_llvm(t) for t in argtys]
    lfty = lc.Type.function(lc.Type.void(), largs + [lc.Type.pointer(lret)])
    lfunc = lmod.add_function(lfty, name=name)
    edf = ExternalDeviceFunction(name, lmod, lfunc, retty, argtys)
    return edf

def compile_common(func, retty, argtys):
    # symbolic interpretation
    se = symbolic.SymbolicExecution(func)
    se.visit()
    #print se.dump()

    argspec = inspect.getargspec(func)
    assert not argspec.keywords, "does not support keywords"
    assert not argspec.varargs, "does not support varargs"
    assert not argspec.defaults, "does not support defaults"

    globals = func.func_globals
    
    # bind scalar constants
    bind_scalar_constants(se.blocks, globals, intp=CUDA_ADDR_SIZE)

    # type infernece
    tydict = dict(zip(argspec.args, argtys))
    tydict[''] = retty

    infer = typing.Infer(se.blocks, tydict, globals, intp=CUDA_ADDR_SIZE,
                         extended_globals=cudapy_global_typing_ext,
                         extended_calls=cudapy_call_typing_ext)

    typemap = infer.infer()

    # code generation
    name = get_func_name(func)
    cg = codegen.CodeGen(name, se.blocks, typemap, globals,
                         argtys, retty, intp=CUDA_ADDR_SIZE,
                         extended_globals=cudapy_global_codegen_ext,
                         extended_calls=cudapy_call_codegen_ext)
    lfunc = cg.generate()
    gvars = cg.extern_globals
    if gvars:
        raise NotImplementedError(
                            "binding of external arguments are not supported")

    #print lfunc.module

    lfunc.module.verify()

    return lfunc.module, lfunc


def to_ptx(lfunc):
    context = driver.get_or_create_context()
    cc_major, cc_minor = context.device.COMPUTE_CAPABILITY
    arch = nvvm.get_arch_option(cc_major, cc_minor)
    nvvm.fix_data_layout(lfunc.module)
    nvvm.set_cuda_kernel(lfunc)
    ptx = nvvm.llvm_to_ptx(str(lfunc.module), opt=3, arch=arch)
    return ptx

class DeviceFunction(object):
    def __init__(self, func, lmod, lfunc, retty, argtys):
        self.func = func
        self.args = tuple(argtys)
        self.return_type = retty
        self._npm_context_ = lmod, lfunc, self.return_type, self.args

    def __repr__(self):
        args = (self.return_type or 'void', self.args)
        return '<cuda device function %s%s>' % args

class ExternalDeviceFunction(object):
    def __init__(self, name, lmod, lfunc, retty, argtys):
        self.name = name
        self.args = tuple(argtys)
        self.return_type = retty
        self._npm_context_ = lmod, lfunc, self.return_type, self.args

    def __repr__(self):
        args = (self.name, self.return_type or 'void', self.args)
        return '<cuda external device function %s %s%s>' % args