"""Map bounded tree-sitter query matches to exact declaration spans."""


from .registry import LanguageError
from .symbols import NativeSymbolKind as Kind


_ROLES = {kind.value.lower(): kind for kind in Kind if kind is not Kind.MODULE}
_ROLES["owner"] = None


def _end(target, extent, kind, raw):
    if extent == "dart_body" and kind is Kind.FUNCTION:
        following = target.next_named_sibling
        if following is None or following.type != "function_body":
            raise LanguageError("Dart function lacks an adjacent implementation")
        return following.end_byte
    if extent == "asm_label" and kind is Kind.LABEL:
        sibling = target.next_named_sibling
        while sibling is not None:
            if sibling.type == "label":
                return sibling.start_byte
            sibling = sibling.next_named_sibling
        return len(raw)
    return target.end_byte


def declarations(root, raw, path, query, extent, max_symbols):
    from tree_sitter import QueryCursor
    cursor = QueryCursor(query, match_limit=1024)
    matches = cursor.matches(root)
    if cursor.did_exceed_match_limit or len(matches) >= max_symbols:
        raise LanguageError("declaration matches exceed the Gold symbol budget")
    found = []
    for _, captures in matches:
        keys = set(captures).intersection(_ROLES)
        if len(keys) != 1 or len(captures.get("name", [])) != 1:
            raise LanguageError("query did not select one named declaration")
        role = keys.pop()
        if len(captures[role]) != 1:
            raise LanguageError("query selected multiple declaration spans")
        target, name_node = captures[role][0], captures["name"][0]
        if not target.start_byte <= name_node.start_byte < name_node.end_byte <= target.end_byte:
            raise LanguageError("name lies outside its declaration")
        name = name_node.text.decode("utf-8")
        if not name or len(name) > 512 or any(ord(ch) < 32 for ch in name):
            raise LanguageError("declaration name is invalid")
        kind = _ROLES[role]
        found.append((target.start_byte, _end(target, extent, kind, raw), name, kind))
    result = [(path, Kind.MODULE, 0, len(raw))]
    names, scopes = {path}, []
    for start, end, name, kind in sorted(found, key=lambda item: (item[0], -item[1])):
        while scopes and scopes[-1][1] <= start:
            scopes.pop()
        if scopes and end > scopes[-1][1]:
            raise LanguageError("declaration boundaries overlap")
        qualified = ".".join([*(scope[0] for scope in scopes), name])
        if kind is not None:
            if qualified in names:
                raise LanguageError("ambiguous native declaration: " + qualified)
            names.add(qualified)
            if kind is Kind.FUNCTION and scopes and scopes[-1][2] in {None, Kind.CLASS}:
                kind = Kind.METHOD
            result.append((qualified, kind, start, end))
        if kind in {None, Kind.CLASS, Kind.FUNCTION, Kind.METHOD}:
            scopes.append((name, end, kind))
    return result
