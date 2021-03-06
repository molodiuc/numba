from __future__ import print_function, division, absolute_import
import numpy as np
from timeit import default_timer as time
from numba import cuda, float32
from numba.cuda.testing import unittest

bpg = 50
tpb = 32
n = bpg * tpb
SM_SIZE = (tpb, tpb)


@cuda.jit(argtypes=[float32[:, ::1], float32[:, ::1], float32[:, ::1]])
def cu_square_matrix_mul(A, B, C):
    sA = cuda.shared.array(shape=SM_SIZE, dtype=float32)
    sB = cuda.shared.array(shape=(tpb, tpb), dtype=float32)

    tx = cuda.threadIdx.x
    ty = cuda.threadIdx.y
    bx = cuda.blockIdx.x
    by = cuda.blockIdx.y
    bw = cuda.blockDim.x
    bh = cuda.blockDim.y

    x = tx + bx * bw
    y = ty + by * bh

    acc = float32(0)  # forces all the math to be f32
    for i in range(bpg):
        if x < n and y < n:
            sA[ty, tx] = A[y, tx + i * tpb]
            sB[ty, tx] = B[ty + i * tpb, x]

        cuda.syncthreads()

        if x < n and y < n:
            for j in range(tpb):
                acc += sA[ty, j] * sB[j, tx]

        cuda.syncthreads()

    if x < n and y < n:
        C[y, x] = acc


class TestCudaMatMul(unittest.TestCase):
    def test_func(self):
        A = np.array(np.random.random((n, n)), dtype=np.float32)
        B = np.array(np.random.random((n, n)), dtype=np.float32)
        C = np.empty_like(A)

        s = time()
        stream = cuda.stream()
        with stream.auto_synchronize():
            dA = cuda.to_device(A, stream)
            dB = cuda.to_device(B, stream)
            dC = cuda.to_device(C, stream)
            cu_square_matrix_mul[(bpg, bpg), (tpb, tpb), stream](dA, dB, dC)
            dC.copy_to_host(C, stream)

        e = time()
        tcuda = e - s

        # Host compute
        Amat = np.matrix(A)
        Bmat = np.matrix(B)

        s = time()
        Cans = Amat * Bmat
        e = time()
        tcpu = e - s

        # Check result
        self.assertTrue(np.allclose(C, Cans))


if __name__ == '__main__':
    unittest.main()
