import re
import os
from pathlib import Path

with open('run_pipeline.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add langfuse import and load_dotenv
import_block = '''from pathlib import Path
from dotenv import load_dotenv
from langfuse import Langfuse

# Load .env from csv_batch_tool
load_dotenv(ROOT / "csv_batch_tool" / ".env")
'''
content = content.replace('from pathlib import Path', import_block)

# 2. Replace load_prompts
new_load_prompts = '''def load_prompts() -> dict:
    try:
        lf = Langfuse()
        prompt_obj = lf.get_prompt("extraction_prompt")
        log("Successfully loaded extraction_prompt from Langfuse.")
        return {"system_role": prompt_obj.prompt}
    except Exception as e:
        log(f"WARNING: Failed to load extraction_prompt from Langfuse: {e} — Go tool will use its built-in prompts")
        return {}'''

pattern = re.compile(r'def load_prompts\(\) -> dict:\n.*?def write_prompts_override', re.DOTALL)
content = pattern.sub(new_load_prompts + '\n\n\ndef write_prompts_override', content)

with open('run_pipeline.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Successfully updated run_pipeline.py')
