"""Preserve published C++, JavaScript and Go symbol identities."""
from .registry import LanguageError as SyntaxUnavailable
from .symbols import NativeSymbolKind


def _field_text(node, field):
    child = node.child_by_field_name(field)
    return "" if child is None else child.text.decode("utf-8")


def _cpp_name(declarator):
    while declarator is not None:
        if declarator.type in {"identifier", "field_identifier", "qualified_identifier", "destructor_name", "operator_name"}:
            return declarator.text.decode("utf-8").replace("::", ".")
        declarator = declarator.child_by_field_name("declarator")
    return ""


def _receiver(node):
    param = node.child_by_field_name("receiver")
    if param is None or len(param.named_children) != 1:
        return ""
    owner = param.named_children[0].child_by_field_name("type")
    while owner is not None and owner.type in {"pointer_type", "generic_type"}:
        owner = owner.child_by_field_name("type") or (owner.named_children[0] if owner.named_children else None)
    return owner.text.decode("utf-8") if owner is not None and owner.type == "type_identifier" else ""


def _declaration(node, language, class_owner):
    kind = node.type
    if language == "cpp" and kind == "function_definition":
        name = _cpp_name(node.child_by_field_name("declarator"))
        return name, NativeSymbolKind.METHOD if class_owner else NativeSymbolKind.FUNCTION
    if kind in {"function_declaration", "generator_function_declaration"}:
        return _field_text(node, "name"), NativeSymbolKind.FUNCTION
    if kind == "method_declaration" and language == "go":
        owner = _receiver(node)
        if not owner:
            raise SyntaxUnavailable("cannot resolve Go method receiver")
        return owner + "." + _field_text(node, "name"), NativeSymbolKind.METHOD
    if kind == "method_definition":
        name = node.child_by_field_name("name")
        if name is None or name.type not in {"property_identifier", "private_property_identifier"}:
            return "", None
        return name.text.decode("utf-8"), NativeSymbolKind.METHOD
    if language == "javascript" and kind in {"variable_declarator", "assignment_expression"}:
        value = node.child_by_field_name("value" if kind == "variable_declarator" else "right")
        if value is not None and value.type in {"arrow_function", "function_expression", "generator_function"}:
            left = node.child_by_field_name("name" if kind == "variable_declarator" else "left")
            if left is not None and left.type == "identifier":
                return left.text.decode("utf-8"), NativeSymbolKind.FUNCTION
            if left is not None and left.type == "member_expression" and _field_text(left, "object") in {"exports", "module.exports"}:
                return _field_text(left, "property"), NativeSymbolKind.FUNCTION
    return "", None


def declarations(root, raw, path, language, max_symbols):
    result = [(path, NativeSymbolKind.MODULE, 0, len(raw))]
    names = {path}
    pending = [(root, (), False)]
    groups = {"class_specifier", "struct_specifier", "class_declaration", "namespace_definition"}
    while pending:
        node, scope, class_owner = pending.pop()
        name, kind = _declaration(node, language, class_owner)
        if node.type in groups:
            name = _field_text(node, "name").replace("::", ".")
            if not name:
                continue
            if node.type == "namespace_definition":
                scope = (*scope, name)
            else:
                kind = NativeSymbolKind.CLASS
                class_owner = True
        elif language == "go" and node.type == "type_spec":
            name, kind = _field_text(node, "name"), NativeSymbolKind.TYPE
        if kind is not None:
            if not name:
                continue
            qualified = ".".join((*scope, name))
            if qualified in names:
                raise SyntaxUnavailable("ambiguous native declaration: " + qualified)
            if len(result) >= max_symbols:
                raise SyntaxUnavailable("native source exceeds the symbol budget")
            names.add(qualified)
            result.append((qualified, kind, node.start_byte, node.end_byte))
            if kind in {NativeSymbolKind.FUNCTION, NativeSymbolKind.METHOD, NativeSymbolKind.TYPE}:
                continue
            scope = (*scope, name)
        if node.type in {"function_expression", "arrow_function", "lambda_expression", "func_literal"}:
            continue
        pending.extend((child, scope, class_owner) for child in reversed(node.named_children))
    return result
