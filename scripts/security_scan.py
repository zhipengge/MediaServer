#!/usr/bin/env python3
"""Scan the repository for personal information before publishing it.

Checks for private IPs, e-mail addresses, home-directory paths, phone numbers,
API tokens, SSH keys and personal hostnames.  Documentation examples must use
reserved TEST-NET addresses (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) or
``example.com`` domains instead of real infrastructure.

    pipenv run python scripts/security_scan.py .
    git ls-files -z | xargs -0 pipenv run python scripts/security_scan.py --files

Exit code 1 when anything suspicious is found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".css", ".html", ".jinja", ".j2", ".md", ".txt", ".yml",
    ".yaml", ".toml", ".ini", ".cfg", ".conf", ".sh", ".service", ".example", ".json",
    ".xml", ".svg", ".gitignore", ".dockerignore", ".lock",
}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
             "data", "media", "node_modules", "dist", "build", ".mypy_cache", "vendor"}
SKIP_FILES = {"Pipfile.lock", "security_scan.py"}

# Reserved documentation ranges are allowed to appear.
ALLOWED_IPS = {
    "0.0.0.0", "127.0.0.1", "255.255.255.255",
    "192.0.2.10", "192.0.2.20", "192.0.2.1", "198.51.100.10", "203.0.113.10",
    "192.0.2.0", "198.51.100.0", "203.0.113.0",
}

PRIVATE_IP = re.compile(
    r"(?<![\d.])("
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
    r"169\.254\.\d{1,3}\.\d{1,3}"
    r")(?![\d.])"
)
PUBLIC_IP = re.compile(
    r"(?<![\d.])((?:[1-9]?\d|1\d\d|2[0-4]\d|25[0-5])"
    r"(?:\.(?:[1-9]?\d|1\d\d|2[0-4]\d|25[0-5])){3})(?![\d.])"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
HOME_PATH = re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)")
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
SECRETS = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----)"
)
HOSTNAME = re.compile(
    r"(?<![A-Za-z0-9.-])(?:lisa|dgx-0\d|spark-0cc8|gezhipeng-[\w-]+)(?![A-Za-z0-9.-])",
    re.I,
)

ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "users.noreply.github.com"}
ALLOWED_HOME_NAMES = {"user", "mediaserver", "app", "run", "media", "nas", "someone", "tunnel"}


def iter_files(root: Path, explicit: list[str] | None) -> list[Path]:
    if explicit:
        return [Path(name) for name in explicit if Path(name).is_file()]
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
                ".gitignore", ".dockerignore", ".env.example", "Pipfile", "Makefile"}:
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
        except OSError:
            continue
        files.append(path)
    return files


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    try:
        text = path.read_text("utf-8", "replace")
    except OSError:
        return []
    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "#")) and "noqa: personal" in stripped:
            continue
        for match in PRIVATE_IP.finditer(line):
            value = match.group(1)
            if value not in ALLOWED_IPS:
                findings.append((lineno, "private-ip", value))
        for match in PUBLIC_IP.finditer(line):
            value = match.group(1)
            if value in ALLOWED_IPS:
                continue
            octets = [int(part) for part in value.split(".")]
            if octets[0] == 127 or value.startswith(("192.0.2.", "198.51.100.", "203.0.113.")):
                continue
            findings.append((lineno, "public-ip", value))
        for match in EMAIL.finditer(line):
            domain = match.group(1).lower()
            if domain in ALLOWED_EMAIL_DOMAINS or any(
                    domain.endswith("." + allowed) for allowed in ALLOWED_EMAIL_DOMAINS):
                continue
            findings.append((lineno, "email", match.group(0)))
        for match in HOME_PATH.finditer(line):
            if match.group(1) not in ALLOWED_HOME_NAMES:
                findings.append((lineno, "home-path", match.group(0)))
        for match in PHONE.finditer(line):
            findings.append((lineno, "phone", match.group(0)))
        for match in SECRETS.finditer(line):
            findings.append((lineno, "secret", match.group(1)[:24] + "…"))
        for match in HOSTNAME.finditer(line):
            findings.append((lineno, "personal-host", match.group(0)))
    return findings


def check_git_tracking(root: Path) -> list[str]:
    problems: list[str] = []
    try:
        output = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                                text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return problems
    if output.returncode != 0:
        return problems
    for name in output.stdout.splitlines():
        if name == ".env" or name.startswith(("data/", "media/")) or name.endswith((".sqlite3", ".log")):
            problems.append(f"git tracks runtime/secret file: {name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for personal information before publishing")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    parser.add_argument("--files", nargs="*", help="explicit files instead of a directory walk")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    files = iter_files(root, args.files)
    total = 0
    for path in files:
        findings = scan_file(path)
        if not findings:
            continue
        rel = path.relative_to(root) if path.is_relative_to(root) else path
        for lineno, kind, value in findings:
            print(f"{rel}:{lineno}: {kind}: {value}")
            total += 1
    total += len(check_git_tracking(root))
    for problem in check_git_tracking(root):
        print(problem)

    print(f"\n扫描 {len(files)} 个文件，发现 {total} 处可疑信息。")
    if total:
        print("改用 example.com / 192.0.2.x 等保留地址，或把真实值放进未跟踪的 .env。")
        return 1
    print("未发现个人信息或秘密。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
