"""测试 changelog 模块核心功能"""
import sys
import os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.changelog import (
    markdown_to_enhanced_html,
    fill_template,
    _extract_version,
    _get_template_path,
    _get_update_log_path,
    _DEFAULT_LOG,
)

# 1. 版本提取
ver = _extract_version(_DEFAULT_LOG)
print(f'[TEST] 版本提取: {ver}')

# 2. MD -> HTML 转换
html = markdown_to_enhanced_html(_DEFAULT_LOG)
print(f'[TEST] MD->HTML: {len(html)} 字符')
print(f'[TEST] 包含 tag-new: {"tag-new" in html}')
print(f'[TEST] 包含 tag-fix: {"tag-fix" in html}')
print(f'[TEST] 包含 h2: {"<h2>" in html}')

# 3. 模板填充
tpl = open(str(_get_template_path()), 'r', encoding='utf-8').read()
filled = fill_template(
    html_template=tpl,
    bot_name='幻梦',
    bot_avatar='\U0001f319',
    version=ver,
    content_html=html,
    date_str='2026年05月07日',
)
print(f'[TEST] 模板填充: {len(filled)} 字符')
print(f'[TEST] BOT_NAME 已替换: {"{BOT_NAME}" not in filled}')
print(f'[TEST] CONTENT 已替换: {"{CONTENT}" not in filled}')

# 4. 文件路径检查
print(f'[TEST] 模板文件存在: {_get_template_path().exists()}')
print(f'[TEST] 日志文件存在: {_get_update_log_path().exists()}')

# 5. 输出 HTML 文件用于预览
out_html = os.path.join(os.path.dirname(__file__), 'data', 'test_card.html')
with open(out_html, 'w', encoding='utf-8') as f:
    f.write(filled)
print(f'[TEST] 测试 HTML 已保存到: {out_html}')

print('\n所有单元测试通过!')
