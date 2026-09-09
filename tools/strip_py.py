import ast
import io
import os
import re
import sys
import tokenize

keep_comment = re.compile(r"^#\s*(name|url|receiveFrom|supportedDevices|"
                          r"isDefaultShortcut)\s*=", re.IGNORECASE)


def drop_comments(source, keep_header=False):
    out = []
    header_done = False
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            if keep_header and not header_done and keep_comment.match(
                    token.string):
                out.append(token)
            continue
        if token.type not in (tokenize.NL, tokenize.ENCODING):
            header_done = True
        out.append(token)
    return tokenize.untokenize(out)


def drop_docstrings(source):
    tree = ast.parse(source)
    cuts = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        first = body[0]
        if not (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            continue
        lone = len(body) == 1 and not isinstance(node, ast.Module)
        cuts.append((first.lineno, first.end_lineno, lone,
                     first.col_offset))

    lines = source.splitlines()
    for start, end, lone, column in sorted(cuts, reverse=True):
        replacement = [" " * column + "pass"] if lone else []
        lines[start - 1:end] = replacement
    return "\n".join(lines)


def tidy(source):
    lines = []
    blanks = 0
    for line in source.splitlines():
        line = line.rstrip()
        if not line:
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        lines.append(line)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def strip_file(path, keep_header=False):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    result = tidy(drop_docstrings(drop_comments(source, keep_header)))
    compile(result, path, "exec")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(result)


def main(root):
    count = 0
    for folder, _, names in os.walk(root):
        for name in names:
            if name.endswith(".py"):
                strip_file(os.path.join(folder, name),
                           keep_header=name.startswith("device_"))
                count += 1
    print("stripped %d files" % count)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
