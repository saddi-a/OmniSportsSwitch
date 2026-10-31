import sys
import types

from daemon import gpu


def _fake_torch(available=True, fails=False):
    calls = []
    def empty_cache():
        if fails:
            raise RuntimeError("boom")
        calls.append(1)
    cuda = types.SimpleNamespace(is_available=lambda: available, empty_cache=empty_cache)
    return types.SimpleNamespace(cuda=cuda), calls


def test_empties_the_cuda_cache(monkeypatch):
    torch, calls = _fake_torch()
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert gpu.free_gpu_cache() is True and calls == [1]


def test_no_cuda_or_error_is_harmless(monkeypatch):
    torch, calls = _fake_torch(available=False)
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert gpu.free_gpu_cache() is False and calls == []
    torch, _ = _fake_torch(fails=True)
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert gpu.free_gpu_cache() is False


def test_does_not_import_torch(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert gpu.free_gpu_cache() is False
