import re

try:
    import yaml as _pyyaml
except ImportError:
    _pyyaml = None


class YamlError(Exception):
    pass


_int_pattern = re.compile(r"^[+-]?\d+$")
_float_pattern = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def _strip_comment(line):
    out = []
    quote = None
    previous = ""
    for char in line:
        if quote:
            out.append(char)
            if char == quote and previous != "\\":
                quote = None
        elif char in "\"'":
            quote = char
            out.append(char)
        elif char == "#" and (not out or out[-1] in " \t"):
            break
        else:
            out.append(char)
        previous = char
    return "".join(out).rstrip()


def _scalar(text):
    text = text.strip()
    if not text:
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        body = text[1:-1]
        if text[0] == '"':
            body = (body.replace("\\n", "\n").replace('\\"', '"')
                        .replace("\\\\", "\\"))
        return body
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~", "none"):
        return None
    if text in ("[]",):
        return []
    if text in ("{}",):
        return {}
    if _int_pattern.match(text):
        return int(text)
    if _float_pattern.match(text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def _tokenize(text):
    rows = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise YamlError("line %d is indented with a tab; use spaces"
                            % number)
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        rows.append((indent, line.strip(), number))
    return rows


def _parse_block(rows, start, indent):
    if start >= len(rows):
        return None, start

    if rows[start][1].startswith("- "):
        return _parse_sequence(rows, start, indent)
    if rows[start][1] == "-":
        return _parse_sequence(rows, start, indent)
    return _parse_mapping(rows, start, indent)


def _parse_sequence(rows, start, indent):
    items = []
    index = start
    while index < len(rows):
        row_indent, content, number = rows[index]
        if row_indent < indent:
            break
        if row_indent > indent:
            raise YamlError("unexpected indentation on line %d" % number)
        if not (content == "-" or content.startswith("- ")):
            break

        body = content[1:].strip()
        index += 1
        if not body:
            value, index = _parse_block(rows, index, indent + 2)
            items.append(value)
            continue

        if ":" in body and not body.startswith(("\"", "'")):
            key, _, rest = body.partition(":")
            entry = {key.strip(): _scalar(rest)} if rest.strip() else {}
            child_indent = row_indent + 2
            if not rest.strip():
                value, index = _parse_block(rows, index, child_indent)
                entry = {key.strip(): value}
            while index < len(rows) and rows[index][0] >= child_indent \
                    and not rows[index][1].startswith("- "):
                sub, index = _parse_mapping(rows, index, rows[index][0])
                if isinstance(sub, dict):
                    entry.update(sub)
            items.append(entry)
        else:
            items.append(_scalar(body))
    return items, index


def _parse_mapping(rows, start, indent):
    mapping = {}
    index = start
    while index < len(rows):
        row_indent, content, number = rows[index]
        if row_indent < indent:
            break
        if row_indent > indent:
            raise YamlError("unexpected indentation on line %d" % number)
        if content.startswith("- "):
            break
        if ":" not in content:
            raise YamlError("line %d is not 'key: value'" % number)

        key, _, rest = content.partition(":")
        key = _scalar(key.strip())
        rest = rest.strip()
        index += 1

        if rest:
            mapping[key] = _scalar(rest)
            continue

        if index < len(rows) and rows[index][0] > row_indent:
            value, index = _parse_block(rows, index, rows[index][0])
            mapping[key] = value
        elif index < len(rows) and rows[index][1].startswith("- ") \
                and rows[index][0] == row_indent:
            value, index = _parse_sequence(rows, index, row_indent)
            mapping[key] = value
        else:
            mapping[key] = None
    return mapping, index


def loads(text):
    if _pyyaml is not None:
        try:
            return _pyyaml.safe_load(text) or {}
        except Exception as error:
            raise YamlError(str(error))

    rows = _tokenize(text)
    if not rows:
        return {}
    value, index = _parse_block(rows, 0, rows[0][0])
    if index != len(rows):
        raise YamlError("could not parse from line %d" % rows[index][2])
    return value


_plain_safe = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 _.,()/@+&'!?=-]*$")


def _emit_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if not text:
        return '""'
    reserved = text.lower() in ("true", "false", "null", "yes", "no", "on",
                                "off", "~")
    if reserved or not _plain_safe.match(text) or text != text.strip():
        return '"%s"' % (text.replace("\\", "\\\\").replace('"', '\\"')
                             .replace("\n", "\\n"))
    return text


def _comment_lines(value):
    if isinstance(value, (list, tuple)):
        return [str(line) for line in value]
    return [str(value)]


def dumps(data, indent=0, comments=True):
    pad = " " * indent
    lines = []

    if isinstance(data, dict):


        docs = {key[2:]: value for key, value in data.items()
                if isinstance(key, str) and key.startswith("//") and key != "//"}

        for key, value in data.items():
            if isinstance(key, str) and key.startswith("//"):
                if not comments or key != "//":
                    continue
                for line in _comment_lines(value):
                    lines.append((pad + "# " + line).rstrip())
                continue

            if comments and key in docs:
                for line in _comment_lines(docs[key]):
                    lines.append((pad + "# " + line).rstrip())

            if isinstance(value, dict):
                real = {k: v for k, v in value.items()
                        if not (isinstance(k, str) and k.startswith("//"))}
                if not real and not any(isinstance(k, str) and
                                        k.startswith("//")
                                        for k in value):
                    lines.append("%s%s: {}" % (pad, key))
                    continue
                lines.append("%s%s:" % (pad, key))
                lines.extend(dumps(value, indent + 2, comments).splitlines())
            elif isinstance(value, (list, tuple)):
                if not value:
                    lines.append("%s%s: []" % (pad, key))
                    continue
                lines.append("%s%s:" % (pad, key))
                lines.extend(dumps(value, indent + 2, comments).splitlines())
            else:
                lines.append("%s%s: %s" % (pad, key, _emit_scalar(value)))
        return "\n".join(lines) + ("\n" if indent == 0 and lines else "")

    if isinstance(data, (list, tuple)):
        for item in data:
            if isinstance(item, dict):
                body = dumps(item, indent + 2, comments).splitlines()
                if not body:
                    lines.append("%s- {}" % pad)
                    continue
                first = body[0].lstrip()
                lines.append("%s- %s" % (pad, first))
                lines.extend(body[1:])
            elif isinstance(item, (list, tuple)):
                body = dumps(item, indent + 2, comments).splitlines()
                lines.append("%s-" % pad)
                lines.extend(body)
            else:
                lines.append("%s- %s" % (pad, _emit_scalar(item)))
        return "\n".join(lines) + ("\n" if indent == 0 and lines else "")

    return pad + _emit_scalar(data) + "\n"
