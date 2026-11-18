"""Parse committed or retained source bytes using the registered Gold language profile."""
from .languages.registry import LEGACY_RESOLVER as RESOLVER, LanguageError as SyntaxUnavailable, for_path, parser_spec
from .languages.symbols import NativeSymbolKind
from .languages.original_declarations import declarations as original_declarations
from .languages.query_declarations import declarations as query_declarations

CONTRACT = "synapse.stage4.gold.native-code-binding/v1"


def native_language(path):
    p = for_path(path)
    return p.id if p and p.driver != "python" else None


def resolver_for(path):
    p = for_path(path)
    if p is None or p.driver == "python":
        raise SyntaxUnavailable("no native structural resolver for " + path)
    return p.resolver


def native_spans(raw, path, *, max_symbols=128):
    p = for_path(path)
    if p is None or p.driver == "python":
        raise SyntaxUnavailable("no native structural resolver for " + path)
    grammar, query = parser_spec(p.id, p.grammar_for(path))
    from tree_sitter import Parser
    raw.decode("utf-8")
    root = Parser(grammar).parse(raw).root_node
    if root.has_error or root.descendant_count > 100_000:
        raise SyntaxUnavailable("invalid source or syntax tree exceeds the node budget")
    if p.driver == "legacy":
        return original_declarations(root, raw, path, p.id, max_symbols)
    return query_declarations(root, raw, path, query, p.extent, max_symbols)
