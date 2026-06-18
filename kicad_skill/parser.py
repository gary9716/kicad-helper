import re

def parse_sexpr(content: str):
    """
    Parses an S-expression string into nested Python lists of strings.
    Handles double quotes (with escaped characters) and strips comments starting with ';'.
    """
    tokens = []
    i = 0
    n = len(content)
    
    while i < n:
        c = content[i]
        
        # Skip whitespace
        if c.isspace():
            i += 1
            continue
            
        # Skip comments
        if c == ';':
            # Skip until newline
            while i < n and content[i] != '\n':
                i += 1
            continue
            
        # Parentheses
        if c == '(':
            tokens.append('(')
            i += 1
            continue
        elif c == ')':
            tokens.append(')')
            i += 1
            continue
            
        # Quoted string
        if c == '"':
            val = []
            i += 1
            while i < n:
                cc = content[i]
                if cc == '"':
                    i += 1
                    break
                elif cc == '\\':
                    if i + 1 < n:
                        val.append(content[i+1])
                        i += 2
                    else:
                        val.append('\\')
                        i += 1
                else:
                    val.append(cc)
                    i += 1
            tokens.append(''.join(val))
            continue
            
        # Unquoted symbol/number/identifier
        start = i
        while i < n and not content[i].isspace() and content[i] not in '()':
            if content[i] == ';':
                break
            i += 1
        tokens.append(content[start:i])
    
    # Parse tokens into nested lists
    stack = []
    current = []
    for token in tokens:
        if token == '(':
            stack.append(current)
            current = []
        elif token == ')':
            if not stack:
                raise ValueError("Unexpected ')'")
            parent = stack.pop()
            parent.append(current)
            current = parent
        else:
            current.append(token)
            
    if stack:
        raise ValueError("Missing ')'")
        
    return current[0] if current else []

def format_sexpr(node, indent_level=0, parent_tag=None, is_tag=False, child_idx=0) -> str:
    """
    Formats nested Python lists back to KiCad S-expression format.
    Ensures correct indentation and quotes strings where necessary.
    """
    indent = "\t" * indent_level
    if not isinstance(node, list):
        if isinstance(node, str):
            if not node:
                return '""'
            
            # Check if this string must be quoted based on parent tag and index
            QUOTED_VAL_TAGS = {'property', 'name', 'number', 'symbol', 'uuid', 'generator', 'generator_version', 'lib_id'}
            VALID_PIN_TYPES = {
                'input', 'output', 'bidirectional', 'tri_state', 'passive',
                'free', 'unspecified', 'power_in', 'power_out', 'open_collector',
                'open_emitter', 'no_connect'
            }
            should_quote = False
            if not is_tag:
                if parent_tag in QUOTED_VAL_TAGS:
                    should_quote = True
                elif parent_tag == 'paper' and child_idx == 1:
                    should_quote = True
                elif parent_tag in ('path', 'page') and child_idx == 1:
                    should_quote = True
                elif parent_tag == 'pin' and child_idx == 1:
                    # If it's a pin electrical type keyword under symbol pin, do not quote.
                    # Otherwise (sheet pin name), quote it.
                    if node not in VALID_PIN_TYPES:
                        should_quote = True
                
            # Check if it needs quotes
            # KiCad unquoted strings must only contain certain characters
            # and cannot conflict with keywords or numbers containing spaces/parens.
            if should_quote or any(c in node for c in ' ()"\t\n\r;#') or not re.match(r'^[a-zA-Z0-9_./\-+:]+$', node):
                escaped = node.replace('\\', '\\\\').replace('"', '\\"')
                return f'"{escaped}"'
            return node
        return str(node)
    
    if not node:
        return "()"
    
    tag = node[0]
    # Check if we should inline this node
    inline_tags = {
        'at', 'xy', 'start', 'end', 'size', 'stroke', 'fill', 'uuid', 'font', 
        'justify', 'width', 'type', 'color', 'style', 'pts', 'grid', 'paper', 
        'version', 'generator', 'generator_version', 'pin_names', 'pin_numbers',
        'exclude_from_sim', 'in_bom', 'on_board', 'in_pos_files', 'dnp', 
        'fields_autoplaced', 'mirror'
    }
    
    # Check if this node has any complex nested structure (non-inline tags)
    has_complex_nested = False
    for x in node[1:]:
        if isinstance(x, list):
            if len(x) > 0 and x[0] not in inline_tags:
                has_complex_nested = True
                break
    
    should_inline = (tag in inline_tags or len(node) <= 4) and not has_complex_nested
    
    if should_inline:
        parts = []
        for idx, x in enumerate(node):
            parts.append(format_sexpr(x, 0, parent_tag=tag, is_tag=(idx == 0), child_idx=idx))
        return "(" + " ".join(parts) + ")"
    else:
        parts = []
        parts.append(f"({format_sexpr(node[0], 0, parent_tag=tag, is_tag=True, child_idx=0)}")
        for idx, x in enumerate(node[1:], 1):
            child_str = format_sexpr(x, indent_level + 1, parent_tag=tag, is_tag=False, child_idx=idx)
            if child_str.startswith("("):
                parts.append("\n" + "\t" * (indent_level + 1) + child_str)
            else:
                parts.append(" " + child_str)
        
        # Determine if closing parenthesis goes on a new line
        last_is_nested = False
        if len(node) > 1:
            last_str = format_sexpr(node[-1], indent_level + 1, parent_tag=tag, is_tag=False, child_idx=len(node)-1)
            if last_str.startswith("("):
                last_is_nested = True
                
        if last_is_nested:
            return "".join(parts) + "\n" + indent + ")"
        else:
            return "".join(parts) + ")"
