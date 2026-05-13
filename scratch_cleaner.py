import re
import os

with open('Auditor/31-03_audit_products.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add Langfuse init at the top
import_block = '''from audit_pydantic import ProductAuditReport

warnings.filterwarnings("ignore")
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "csv_batch_tool", ".env")
from dotenv import load_dotenv
load_dotenv(env_path, override=True)
load_dotenv()

from langfuse import Langfuse
lf_client = Langfuse()'''

content = content.replace('from audit_pydantic import ProductAuditReport\n\nwarnings.filterwarnings("ignore")\nload_dotenv()', import_block)

# 2. Replace the build_audit_prompt logic
prompt_logic = '''
    # Fetch prompt from Langfuse
    try:
        prompt = lf_client.get_prompt("product_audit_prompt")
        return prompt.compile(
            checklist_block=checklist_block,
            thinking_1=thinking_1,
            thinking_2=thinking_2,
            llm_1_name=llm_1_name,
            llm_2_name=llm_2_name
        )
    except Exception as e:
        _audit_log(f"CRITICAL ERROR: Failed to load product_audit_prompt from Langfuse: {e}")
        sys.exit(1)
'''

# Find def build_audit_prompt and replace it until def _load_existing_results
pattern = re.compile(r'(def build_audit_prompt\([^)]+\) -> str:\n.*?)(    return f"""\n# PRODUCTS AUDIT AGENT)(.*?)(def _load_existing_results)', re.DOTALL)

def repl(m):
    return m.group(1) + prompt_logic + '\n\n\n' + m.group(4)

new_content = pattern.sub(repl, content)

with open('Auditor/31-03_audit_products.py', 'w', encoding='utf-8') as f:
    f.write(new_content)
print('Successfully cleaned up 31-03_audit_products.py')
