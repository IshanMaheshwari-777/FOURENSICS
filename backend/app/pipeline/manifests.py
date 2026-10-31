"""Dependency manifests as evidence. Reports about dependencies ("the 4.x branch pins body-parser ~1.20.5",
"requires requests<3") are settled by the manifest files themselves, not by changelogs or docs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MANIFESTS = {
    "package.json", "pyproject.toml", "setup.cfg", "setup.py", "go.mod", "Cargo.toml", "Gemfile", "pom.xml",
    "build.gradle", "build.gradle.kts", "composer.json", "Pipfile", "environment.yml",
}
MAX_DEPTH = 3  # root and a couple of levels (monorepo packages)

# A dependency declaration line, per manifest format.
_JSON = re.compile(r'^\s*"(?P<name>@?[\w.\-/]+)"\s*:\s*"(?P<spec>[^"]*)"')
# requirements / pyproject entries need a real version operator, so `key = value` lines don't count
_PEP508 = re.compile(r'^\s*"?(?P<name>[A-Za-z][\w.\-]*)(?:\[[^\]]*\])?\s*'
                     r'(?P<spec>(?:===|==|>=|<=|~=|!=|[<>^~])\s*[\w.*]+[^";#]*)')
_GO = re.compile(r'^\s*(?:require\s+)?(?P<name>[\w.\-]+/[\w.\-/]+)\s+(?P<spec>v[\w.\-+]+)')
_CARGO = re.compile(r'^\s*(?P<name>[\w\-]+)\s*=\s*(?P<spec>"[^"]*"|\{[^}]*\})')
_GEM = re.compile(r"^\s*gem\s+['\"](?P<name>[\w\-]+)['\"](?P<spec>.*)$")
_POM = re.compile(r"<artifactId>(?P<name>[\w.\-]+)</artifactId>(?P<spec>)")
_GRADLE = re.compile(r"['\"][\w.\-]+:(?P<name>[\w.\-]+):(?P<spec>[\w.\-+]+)['\"]")


def _format(name: str) -> list[re.Pattern]:
    if name.endswith(".json"):
        return [_JSON]
    if name == "go.mod":
        return [_GO]
    if name == "Cargo.toml":
        return [_CARGO]
    if name == "Gemfile":
        return [_GEM]
    if name == "pom.xml":
        return [_POM]
    if name.startswith("build.gradle"):
        return [_GRADLE]
    return [_PEP508]  # requirements*.txt, pyproject.toml, setup.cfg, setup.py, Pipfile, environment.yml


_JSON_DEP_BLOCK = re.compile(
    r'"(dependencies|devDependencies|peerDependencies|optionalDependencies|require|require-dev)"\s*:\s*\{')


@dataclass
class Declaration:
    name: str
    path: str
    line: int
    text: str


def find_manifests(files: set[str]) -> list[str]:
    out = []
    for rel in files:
        parts = rel.split("/")
        if len(parts) <= MAX_DEPTH and (parts[-1] in MANIFESTS or re.fullmatch(r"requirements[\w.\-]*\.txt", parts[-1])):
            if "node_modules" not in parts and "vendor" not in parts:
                out.append(rel)
    return sorted(out, key=lambda p: (p.count("/"), p))[:30]


def declarations(root: Path, files: set[str]) -> list[Declaration]:
    found: list[Declaration] = []
    for rel in find_manifests(files):
        try:
            path = root / rel
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                continue
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue  # outside a sparse checkout
        is_json = rel.endswith(".json")
        is_cargo = rel.endswith("Cargo.toml")
        in_deps = False
        for i, text in enumerate(lines, 1):
            if is_cargo:
                # Only [dependencies], [dev-dependencies], [workspace.dependencies], [target.x.dependencies]...
                header = re.match(r"^\s*\[([^\]]+)\]", text)
                if header:
                    in_deps = "dependencies" in header.group(1)
                    continue
                if not in_deps:
                    continue
            if is_json:
                # Only entries inside dependency objects; "scripts", "name" and the like are not packages.
                if _JSON_DEP_BLOCK.search(text):
                    in_deps = True
                    continue
                if in_deps and text.strip().startswith("}"):
                    in_deps = False
                if not in_deps:
                    continue
            for rx in _format(rel.rsplit("/", 1)[-1]):
                m = rx.search(text)
                if m:
                    found.append(Declaration(m.group("name"), rel, i, text.strip()[:200]))
                    break
    return found


def mentioned(decls: list[Declaration], claim_text: str) -> list[Declaration]:
    """Declarations whose package name the claim mentions (whole word, case-insensitive)."""
    text = claim_text.lower()
    out, seen = [], set()
    for d in decls:
        name = d.name.lower()
        if len(name) < 3 or name in ("name", "version", "description", "license", "main", "type", "scripts"):
            continue
        if re.search(r"(?<![\w@/.-])" + re.escape(name) + r"(?![\w-])", text) and (d.path, d.line) not in seen:
            seen.add((d.path, d.line))
            out.append(d)
    return out[:4]
