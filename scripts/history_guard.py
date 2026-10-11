"""Read-only retained-history integrity and append-only Git comparison.

Publication plumbing, not a FHIR validator. Historical checks follow the reviewed
ABA_Wearable history.mjs/readArchive contract; see HISTORY_SAFEGUARD.md.
"""

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import zlib

ROOT = "publication-history/"
RELEASES = "ig/verification/releases/"
PACKAGE = "net.speirotech.fhir.verification"
CANONICAL = "https://fhir.speirotech.net"
IG = "ImplementationGuide-speiro-verification.json"
SHA = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
MAX_BYTES = 64 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def document(data, label):
    try:
        value = json.loads(
            data,
            object_pairs_hook=object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Invalid JSON constant: {value}")
            ),
        )
        require(isinstance(value, dict), "Expected JSON object")
        return value
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{label}: {error}") from error


def version(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    require(match, f"Invalid release version: {value!r}")
    for part in (match[4] or "").split("."):
        require(
            not (part.isdigit() and len(part) > 1 and part.startswith("0")),
            f"Invalid release version: {value!r}",
        )


def archive(data):
    # No extraction; bounded decompression and ordinary regular tar records only.
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        tar = stream.read(16 * 1024 * 1024 + 1)
    require(len(tar) <= 16 * 1024 * 1024, "Archive exceeds 16 MiB")
    entries = {}
    offset = 0
    while offset + 512 <= len(tar):
        header = tar[offset : offset + 512]
        if not any(header):
            break

        def field(start, length, header=header):
            raw = header[start : start + length]
            before, _, after = raw.partition(b"\0")
            require(not any(after), "Nonzero bytes after tar field terminator")
            return before.decode("ascii")

        name = field(0, 100)
        require(not field(345, 155), "Unexpected tar prefix")
        require(field(156, 1) in ("", "0"), f"Non-regular archive member: {name}")
        require(not field(157, 100), f"Archive link target: {name}")
        require(
            name.startswith("package/")
            and all(
                re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in (".", "..")
                for part in name.split("/")
            ),
            f"Unsafe archive path: {name}",
        )
        require(name not in entries, f"Duplicate archive member: {name}")
        size_text = field(124, 12).strip()
        require(re.fullmatch(r"[0-7]+", size_text), f"Invalid tar size: {name}")
        size = int(size_text, 8)
        checksum_text = header[148:156].decode("ascii").strip("\0 ")
        require(re.fullmatch(r"[0-7]+", checksum_text), f"Invalid checksum: {name}")
        require(
            sum(header[:148]) + 8 * 32 + sum(header[156:]) == int(checksum_text, 8),
            f"Tar checksum mismatch: {name}",
        )
        end = offset + 512 + size
        require(end <= len(tar), f"Truncated archive member: {name}")
        entries[name] = tar[offset + 512 : end]
        offset += 512 + ((size + 511) // 512) * 512
    require(
        len(tar) - offset >= 1024 and not any(tar[offset:]),
        "Invalid archive terminator or trailing data",
    )
    return entries


def validate(files):
    require("index.json" in files, "Missing publication-history/index.json")
    index = document(files["index.json"], "index.json")
    require(index.get("packageId") == PACKAGE, "index.json: wrong packageId")
    require(
        isinstance(index.get("releases"), list), "index.json: releases must be a list"
    )
    entries = {}
    for entry in index["releases"]:
        require(isinstance(entry, dict), "index.json: release entry must be an object")
        v = entry.get("version")
        version(v)
        require(v not in entries, f"Duplicate release version: {v}")
        require(
            isinstance(entry.get("manifestSha256"), str)
            and SHA.fullmatch(entry["manifestSha256"]),
            f"{v}: invalid manifestSha256",
        )
        entries[v] = entry
    directories = set()
    for path in files:
        if path in ("index.json", ".gitattributes"):
            continue
        require(path.startswith(RELEASES), f"Unexpected history file: {path}")
        parts = path[len(RELEASES) :].split("/")
        require(len(parts) == 2, f"Invalid retained file path: {path}")
        directories.add(parts[0])
    require(
        directories == set(entries),
        f"Release directories/index mismatch; missing={sorted(set(entries) - directories)}, "
        f"unindexed={sorted(directories - set(entries))}",
    )
    for v, entry in entries.items():
        prefix = RELEASES + v + "/"
        label = prefix + "manifest.json"
        try:
            require(label in files, f"Missing {label}")
            require(
                digest(files[label]) == entry["manifestSha256"],
                "Manifest hash mismatch",
            )
            manifest = document(files[label], label)
            require(
                manifest.get("packageId") == PACKAGE,
                "Manifest package identity mismatch",
            )
            require(manifest.get("version") == v, "Manifest version mismatch")
            hashes = manifest.get("files")
            require(isinstance(hashes, dict), "Manifest files must be an object")
            for leaf in (IG, "index.html", "package.tgz"):
                require(prefix + leaf in hashes, f"Missing required file: {leaf}")
            actual = {p for p in files if p.startswith(prefix)}
            require(actual == set(hashes) | {label}, "Manifest file inventory mismatch")
            leaves = {}
            for path, expected in hashes.items():
                leaf = path[len(prefix) :]
                require(
                    path.startswith(prefix)
                    and re.fullmatch(
                        r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:html|json)|package\.tgz)",
                        leaf,
                    )
                    and path != label,
                    f"Unsafe manifest file path: {path}",
                )
                require(
                    isinstance(expected, str) and SHA.fullmatch(expected),
                    f"Invalid hash: {path}",
                )
                require(digest(files[path]) == expected, f"File hash mismatch: {path}")
                leaves[leaf] = files[path]
            packed = archive(leaves["package.tgz"])
            for name in (
                "package/package.json",
                "package/" + IG,
                "package/other/spec.internals",
            ):
                require(name in packed, f"Missing archive member: {name}")
            metadata = document(packed["package/package.json"], "package/package.json")
            for key, expected in {
                "name": PACKAGE,
                "version": v,
                "canonical": CANONICAL,
                "url": CANONICAL + "/ig/verification/releases/" + v,
            }.items():
                require(metadata.get(key) == expected, f"Package {key} mismatch")
            ig = document(packed["package/" + IG], IG)
            for key, expected in {
                "resourceType": "ImplementationGuide",
                "id": "speiro-verification",
                "url": CANONICAL + "/ImplementationGuide/speiro-verification",
                "packageId": PACKAGE,
                "version": v,
            }.items():
                require(ig.get(key) == expected, f"IG {key} mismatch")
            require(
                leaves[IG] == packed["package/" + IG], "Stored/packaged IG bytes differ"
            )
            spec = document(packed["package/other/spec.internals"], "spec.internals")
            require(isinstance(spec.get("targets"), list), "Invalid spec.targets")
            require(isinstance(spec.get("paths"), dict), "Invalid spec.paths")
            for target in spec["targets"] + list(spec["paths"].values()):
                require(
                    isinstance(target, str) and target in leaves,
                    f"Missing render target: {target!r}",
                )
        except (ValueError, OSError, EOFError, zlib.error) as error:
            raise ValueError(f"Release {v}: {error}") from error
    return index, entries


def compare(base, proposed):
    old_index, old_entries = validate(base)
    new_index, new_entries = validate(proposed)
    for key, value in old_index.items():
        if key != "releases":
            require(
                key in new_index
                and json.dumps(new_index[key], sort_keys=True)
                == json.dumps(value, sort_keys=True),
                f"index.json: changed field {key}",
            )
    for v, entry in old_entries.items():
        require(v in new_entries, f"Removed existing index entry: {v}")
        require(
            json.dumps(new_entries[v], sort_keys=True)
            == json.dumps(entry, sort_keys=True),
            f"Changed existing index entry: {v}",
        )
    for path, data in base.items():
        if path == "index.json":
            continue
        require(path in proposed, f"Deleted or renamed historical file: {path}")
        require(proposed[path] == data, f"Modified historical file: {path}")
    # Even byte-identical extra files would change an already retained release.
    for path in proposed.keys() - base.keys():
        if path.startswith(RELEASES):
            require(
                path[len(RELEASES) :].split("/")[0] not in old_entries,
                f"Added file to existing release: {path}",
            )
    return sorted(new_entries)


def git_history(repo, commit):
    require(
        re.fullmatch(r"[0-9a-f]{40}", commit), "Expected exact 40-character commit SHA"
    )

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])

    require(
        git("cat-file", "-t", commit).strip() == b"commit", f"Not a commit: {commit}"
    )
    files = {}
    total = 0
    for record in git("ls-tree", "-rz", commit, "--", "publication-history").split(
        b"\0"
    ):
        if not record:
            continue
        info, raw_path = record.split(b"\t", 1)
        mode, kind, oid = info.decode().split()
        path = raw_path.decode("utf-8")
        require(
            mode == "100644" and kind == "blob", f"Non-regular history entry: {path}"
        )
        require(path.startswith(ROOT), f"Invalid history root: {path}")
        size = int(git("cat-file", "-s", oid))
        total += size
        require(
            total <= MAX_BYTES,
            "History exceeds 64 MiB validation budget; reviewed limit update required",
        )
        files[path[len(ROOT) :]] = git("cat-file", "blob", oid)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    try:
        versions = compare(
            git_history(args.repo, args.base), git_history(args.repo, args.head)
        )
    except (ValueError, OSError, subprocess.CalledProcessError, zlib.error) as error:
        parser.exit(1, f"Publication history rejected: {error}\n")
    print(
        f"Publication history append-only: PASS; base={args.base}; head={args.head}; versions={versions}"
    )


if __name__ == "__main__":
    main()
