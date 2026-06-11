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

def format_sexpr(node, indent_level=0) -> str:
    """
    Formats nested Python lists back to KiCad S-expression format.
    Ensures correct indentation and quotes strings where necessary.
    """
    indent = "\t" * indent_level
    if not isinstance(node, list):
        if isinstance(node, str):
            if not node:
                return '""'
            # Check if it needs quotes
            # KiCad unquoted strings must only contain certain characters
            # and cannot conflict with keywords or numbers containing spaces/parens.
            if any(c in node for c in ' ()"\t\n\r;#') or node in ['yes', 'no']:
                escaped = node.replace('\\', '\\\\').replace('"', '\\"')
                return f'"{escaped}"'
            # Also if it looks like a symbol that has spaces or is empty
            if not re.match(r'^[a-zA-Z0-9_./\-+:]+$', node):
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
        for x in node:
            parts.append(format_sexpr(x, 0))
        return "(" + " ".join(parts) + ")"
    else:
        parts = []
        parts.append(f"({format_sexpr(node[0], 0)}")
        for x in node[1:]:
            child_str = format_sexpr(x, indent_level + 1)
            if child_str.startswith("("):
                parts.append("\n" + "\t" * (indent_level + 1) + child_str)
            else:
                parts.append(" " + child_str)
        
        # Determine if closing parenthesis goes on a new line
        last_is_nested = False
        if len(node) > 1:
            last_str = format_sexpr(node[-1], indent_level + 1)
            if last_str.startswith("("):
                last_is_nested = True
                
        if last_is_nested:
            return "".join(parts) + "\n" + indent + ")"
        else:
            return "".join(parts) + ")"
