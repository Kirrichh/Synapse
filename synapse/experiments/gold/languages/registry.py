"""Load declarative Gold profiles, validate ownership and select the parser for a path."""
import base64
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from importlib.metadata import PackageNotFoundError, entry_points, version
from importlib.resources import files
import json
from pathlib import PurePosixPath
import re
from types import MappingProxyType

GROUP = "synapse.gold.languages.v1"
PARSER_VERSION = "0.26.1"
PACK_VERSION = "0.13.0"
ORIGINAL = {"cpp": "0.23.4", "javascript": "0.25.0", "go": "0.25.0"}
LEGACY_RESOLVER = "synapse.stage4.gold.native-tree-sitter/v1"


class LanguageError(ValueError):
    """The declared language cannot supply an exact structural interpretation."""


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    suffixes: tuple
    driver: str
    grammar: str
    query: str
    extent: str
    variants: object
    distribution: str
    resolver: str

    def grammar_for(self, path):
        return self.variants.get(PurePosixPath(path).suffix, self.grammar)


def _profile(data, owner, external=False):
    required = {"id", "label", "suffixes", "driver", "grammar", "query", "extent", "variants"}
    if not isinstance(data, dict) or set(data) != required:
        raise LanguageError("invalid language profile fields")
    identifier = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
    suffix = re.compile(r"\.[A-Za-z0-9]+\Z")
    if not all(isinstance(data[k], str) for k in required - {"suffixes", "variants"}):
        raise LanguageError("invalid profile field type")
    if not identifier.fullmatch(data["id"]) or not identifier.fullmatch(data["grammar"]) or not data["label"].strip():
        raise LanguageError("invalid profile identifier or grammar")
    suffixes, variants = data["suffixes"], data["variants"]
    if (not isinstance(suffixes, list) or not 1 <= len(suffixes) <= 64
            or any(not isinstance(s,str) or not suffix.fullmatch(s) for s in suffixes)
            or len(set(suffixes)) != len(suffixes) or not isinstance(variants, dict)
            or any(k not in suffixes or not isinstance(v,str) or not identifier.fullmatch(v) for k,v in variants.items())):
        raise LanguageError("invalid language suffixes or grammar variants")
    driver, query, extent = data["driver"], data["query"], data["extent"]
    if driver not in {"python","legacy","query"} or (external and driver != "query"):
        raise LanguageError("invalid profile driver")
    if (driver == "query") != bool(query.strip()) or len(query.encode()) > 32768 or extent not in {"node","dart_body","asm_label"}:
        raise LanguageError("invalid language query or declaration extent")
    # The original three resolvers keep the existing v1 transport byte-for-byte.
    resolver = LEGACY_RESOLVER
    if driver == "query":
        identity = json.dumps([owner, data, PARSER_VERSION, PACK_VERSION], sort_keys=True, separators=(",",":")).encode()
        resolver = "synapse.stage4.gold.query/v1/" + hashlib.sha256(identity).hexdigest()
    return Profile(data["id"], data["label"], tuple(suffixes), driver, data["grammar"],
                   query, extent, MappingProxyType(dict(variants)), owner, resolver)


def _installed_profile(point):
    parts = re.fullmatch(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*)", point.value)
    if not parts or point.dist is None:
        raise LanguageError("language entry point must name a packaged JSON resource")
    relative = parts[1].replace(".", "/") + "/" + parts[2] + ".json"
    file = next((f for f in point.dist.files or () if str(f) == relative), None)
    if file is None or file.hash is None or file.hash.mode != "sha256" or file.size is None or file.size > 65536:
        raise LanguageError("language resource lacks a bounded wheel RECORD SHA-256")
    path = point.dist.locate_file(file)
    with path.open("rb") as stream:
        raw = stream.read(65537)
    actual = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
    if len(raw) != file.size or actual != file.hash.value:
        raise LanguageError("language profile differs from its installation record")
    owner = point.dist.metadata["Name"] + "==" + point.dist.version
    profile = _profile(json.loads(raw), owner, external=True)
    if profile.id != point.name:
        raise LanguageError("entry point name differs from language identifier")
    return profile


@lru_cache(maxsize=1)
def registry():
    raw = files(__package__).joinpath("profiles.json").read_text(encoding="utf-8")
    profiles = [_profile(value, "synapse-lang") for value in json.loads(raw)]
    try:
        profiles.extend(_installed_profile(ep) for ep in entry_points(group=GROUP))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LanguageError("cannot read installed Gold language profile: " + str(exc)) from exc
    names, suffixes = {}, {}
    for profile in profiles:
        if profile.id in names or any(s in suffixes for s in profile.suffixes):
            raise LanguageError("duplicate language identifier or suffix for " + profile.id)
        names[profile.id] = profile
        suffixes.update(dict.fromkeys(profile.suffixes, profile))
    return MappingProxyType(names), MappingProxyType(suffixes)


def for_path(path):
    return registry()[1].get(PurePosixPath(path).suffix)


@lru_cache(maxsize=128)
def parser_spec(profile_id, grammar):
    p = registry()[0][profile_id]
    try:
        if version("tree-sitter") != PARSER_VERSION:
            raise LanguageError("expected tree-sitter==" + PARSER_VERSION)
        from tree_sitter import Language, Query
        if p.driver == "legacy":
            if version("tree-sitter-" + p.id) != ORIGINAL[p.id]:
                raise LanguageError("expected pinned grammar for " + p.id)
            if p.id == "cpp":
                from tree_sitter_cpp import language
            elif p.id == "go":
                from tree_sitter_go import language
            elif p.id == "javascript":
                from tree_sitter_javascript import language
            else:
                raise LanguageError("unknown original grammar " + p.id)
            return Language(language()), None
        if version("tree-sitter-language-pack") != PACK_VERSION:
            raise LanguageError("expected tree-sitter-language-pack==" + PACK_VERSION)
        from tree_sitter_language_pack import get_language
        language = get_language(grammar)
        query = Query(language, p.query)
        captures = {query.capture_name(i) for i in range(query.capture_count)}
        if "name" not in captures or not captures.intersection({"class","owner","type","function","method","label","view","attribute","rule","element"}):
            raise LanguageError("Gold query must capture a name and a declaration")
        return language, query
    except (ImportError, LookupError, RuntimeError, ValueError, OSError, PackageNotFoundError) as exc:
        raise LanguageError(f"cannot load {p.id} grammar {grammar}: {exc}") from exc


def inventory(check):
    rows = []
    for p in sorted(registry()[0].values(), key=lambda p:p.id):
        row = dict(id=p.id, name=p.label, suffixes=list(p.suffixes), package=p.distribution)
        if check:
            try:
                if p.driver != "python":
                    for grammar in {p.grammar, *p.variants.values()}:
                        parser_spec(p.id, grammar)
                row["status"] = "READY"
            except LanguageError as exc:
                row.update(status="UNAVAILABLE", reason=str(exc))
        rows.append(row)
    return rows
