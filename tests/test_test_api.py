import importlib
import pytest

def test_test_api_exists():
    # TDD red: module must import and expose test_api
    mod_name = 'tests/test_api.py'.replace('/', '.').removesuffix('.py')
    if mod_name.startswith('src.'):
        mod_name = mod_name[4:]
    mod = importlib.import_module(mod_name)
    assert hasattr(mod, 'test_api'), 'test_api' + ' missing'
