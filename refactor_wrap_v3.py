import os
import re

TOOLS_DIR = r"d:\GitHub\echo-framework\12-owui-tools"

def refactor_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add __metadata__ to tool signatures
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if "def " in line and "__user__" in line and "__metadata__" not in line:
            if "):" in line:
                lines[i] = line.replace("):", ", __metadata__: dict = None):")
            elif ") ->" in line:
                lines[i] = line.replace(") ->", ", __metadata__: dict = None) ->")
            else:
                for j in range(i, min(i+10, len(lines))):
                    if "__user__" in lines[j] and "__metadata__" not in lines[j]:
                        lines[j] = lines[j] + ", __metadata__: dict = None"
                        break
    content = '\n'.join(lines)
    
    # 2. Add kwargs to wrap_tool_output and wrap_cascade_output
    kwargs_str = 'user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__'
    
    new_content = ""
    idx = 0
    while idx < len(content):
        match1 = content.find("wrap_tool_output(", idx)
        match2 = content.find("wrap_cascade_output(", idx)
        if match1 == -1 and match2 == -1:
            new_content += content[idx:]
            break
        
        match = match1 if (match1 != -1 and (match2 == -1 or match1 < match2)) else match2
        start_paren = content.find("(", match)
        
        # Prevent matching 'from ... import wrap_tool_output'
        prefix = content[max(0, match-20):match]
        
        paren_count = 1
        curr = start_paren + 1
        while curr < len(content) and paren_count > 0:
            if content[curr] == '(':
                paren_count += 1
            elif content[curr] == ')':
                paren_count -= 1
            curr += 1
            
        end_paren = curr - 1
        call_content = content[start_paren+1:end_paren]
        
        if "import " not in prefix and "metadata=__metadata__" not in call_content and "__metadata__" not in call_content:
            if call_content.strip() == "":
                replacement = kwargs_str
            else:
                # To prevent trailing comma issues leading to double commas, we check it
                if call_content.rstrip().endswith(","):
                    replacement = call_content.rstrip() + " " + kwargs_str
                else:
                    replacement = call_content + ", " + kwargs_str
            new_content += content[idx:start_paren+1] + replacement + ")"
        else:
            new_content += content[idx:end_paren+1]
            
        idx = end_paren + 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

for filename in os.listdir(TOOLS_DIR):
    if filename.endswith(".py"):
        refactor_file(os.path.join(TOOLS_DIR, filename))
        print(f"Refactored {filename}")
