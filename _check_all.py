"""全量模块导入检查"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

modules = [
    'utils.format_lang',
    'utils.message_parser',
    'utils.username', 
    'core.logger',
    'core.config',
    'core.context_manager',
    'services.sender',
    'services.llm',
    'services.image_api',
    'modules.fav',
    'modules.judge',
    'modules.memory',
    'modules.search',
    'modules.weather',
    'modules.changelog',
    'modules.commands',
    'core.dispatcher',
    'core.pipeline',
    'bot',
]

ok = 0
err = 0
for m in modules:
    try:
        __import__(m)
        print(f'  [OK] {m}')
        ok += 1
    except Exception as e:
        print(f'  [FAIL] {m}: {type(e).__name__}: {str(e)[:200]}')
        err += 1

print(f'\nResult: {ok}/{ok+err} OK, {err} FAIL')
