import importlib
import pytest

def test_api_exists():
    # TDD red: module must import and expose api
    mod_name = 'src/remedy/interfaces/api.py'.replace('/', '.').removesuffix('.py')
    if mod_name.startswith('src.'):
        mod_name = mod_name[4:]
    mod = importlib.import_module(mod_name)
    assert hasattr(mod, 'api'), 'api' + ' missing'
