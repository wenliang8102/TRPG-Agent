"""临时迁移脚本 — 为 predefined_characters.py 添加 base_ac 和 class_features"""
import re
import pathlib

f = pathlib.Path("backend/app/calculation/predefined_characters.py")
text = f.read_text(encoding="utf-8")

# "ac": N  ->  "base_ac": N,\n        "ac": N,
def add_base_ac(m):
    val = m.group(1)
    return f'"base_ac": {val},\n        "ac": {val},'

text = re.sub(r'"ac": (\d+),', add_base_ac, text)

# Add class_features before spellcasting_ability
text = text.replace(
    '"spellcasting_ability":',
    '"class_features": [],\n        "spellcasting_ability":'
)

f.write_text(text, encoding="utf-8")
print("Done — predefined_characters.py updated")
