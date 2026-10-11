import contextlib
import copy
import gzip
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from scripts import history_guard as guard


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


def pack(entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, body, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return gzip.compress(data.getvalue())


def release(v="0.1.0", clinical=False):
    prefix = guard.RELEASES + v + "/"
    ig = encode(
        {
            "resourceType": "ImplementationGuide",
            "id": "speiro-verification",
            "url": guard.CANONICAL + "/ImplementationGuide/speiro-verification",
            "packageId": guard.PACKAGE,
            "version": v,
        }
    )
    metadata = {
        "name": guard.PACKAGE,
        "version": v,
        "canonical": guard.CANONICAL,
        "url": guard.CANONICAL + "/ig/verification/releases/" + v,
        "dependencies": {"future.terminology": "99.0.0"},
    }
    entries = [
        ("package/package.json", encode(metadata), tarfile.REGTYPE),
        ("package/" + guard.IG, ig, tarfile.REGTYPE),
        (
            "package/other/spec.internals",
            encode({"targets": ["index.html"], "paths": {}}),
            tarfile.REGTYPE,
        ),
    ]
    if clinical:
        entries.append(
            (
                "package/StructureDefinition-future.json",
                encode({"resourceType": "StructureDefinition"}),
                tarfile.REGTYPE,
            )
        )
    return {
        prefix + guard.IG: ig,
        prefix + "index.html": b"<html>historical guide</html>",
        prefix + "package.tgz": pack(entries),
    }


def reseal(files, v="0.1.0"):
    prefix = guard.RELEASES + v + "/"
    manifest = {
        "packageId": guard.PACKAGE,
        "version": v,
        "files": {
            p: guard.digest(b)
            for p, b in files.items()
            if p.startswith(prefix) and p != prefix + "manifest.json"
        },
    }
    files[prefix + "manifest.json"] = encode(manifest)
    return {
        "version": v,
        "manifestSha256": guard.digest(files[prefix + "manifest.json"]),
    }


def fixture(v="0.1.0", clinical=False):
    files = release(v, clinical)
    entry = reseal(files, v)
    files["index.json"] = encode({"packageId": guard.PACKAGE, "releases": [entry]})
    return files


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.base = fixture()
        self.proposed = copy.deepcopy(self.base)
        self.prefix = guard.RELEASES + "0.1.0/"

    def index(self, mutate):
        value = json.loads(self.proposed["index.json"])
        mutate(value)
        self.proposed["index.json"] = encode(value)

    def reseal(self):
        entry = reseal(self.proposed)
        self.index(lambda x: x.update(releases=[entry]))

    def reject(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            guard.compare(self.base, self.proposed)

    def test_unchanged(self):
        self.assertEqual(guard.compare(self.base, self.proposed), ["0.1.0"])

    def test_formatting_only(self):
        self.proposed["index.json"] = json.dumps(
            json.loads(self.proposed["index.json"]), indent=4
        ).encode()
        guard.compare(self.base, self.proposed)

    def test_file_modification(self):
        self.proposed[self.prefix + "index.html"] = b"changed"
        self.reject("File hash mismatch")

    def test_file_deletion(self):
        del self.proposed[self.prefix + "index.html"]
        self.reject("inventory mismatch")

    def test_file_rename(self):
        self.proposed[self.prefix + "renamed.html"] = self.proposed.pop(
            self.prefix + "index.html"
        )
        self.reject("inventory mismatch")

    def test_entry_removal_with_directory(self):
        self.proposed = {
            "index.json": encode({"packageId": guard.PACKAGE, "releases": []})
        }
        self.reject("Removed existing index entry: 0.1.0")

    def test_existing_entry_extra_field_preserved(self):
        for key, value in [("note", "original"), ("review", {"approved": True})]:
            with self.subTest(key=key):
                old = json.loads(self.base["index.json"])
                old["releases"][0][key] = value
                self.base["index.json"] = encode(old)
                self.reject("Changed existing index entry")
                self.proposed = copy.deepcopy(self.base)

    def test_duplicate_versions(self):
        self.index(lambda x: x["releases"].append(x["releases"][0]))
        self.reject("Duplicate release version")

    def test_malformed_versions(self):
        for v in ["01.0.0", "1.0", "1.0.0-01", "../x", 1, None, "1.0.0\n"]:
            with self.subTest(version=v):
                self.index(lambda x, v=v: x["releases"][0].update(version=v))
                self.reject("Invalid release version")

    def test_missing_release(self):
        self.proposed = {"index.json": self.proposed["index.json"]}
        self.reject("missing=.*0.1.0")

    def test_unindexed_release(self):
        self.proposed.update(release("0.2.0"))
        self.reject("unindexed=.*0.2.0")

    def test_manifest_tamper(self):
        self.proposed[self.prefix + "manifest.json"] += b" "
        self.reject("Manifest hash mismatch")

    def test_coordinated_historical_tamper(self):
        self.proposed[self.prefix + "index.html"] = b"changed but rehashed"
        self.reseal()
        guard.validate(self.proposed)
        self.reject("Changed existing index entry")

    def test_valid_addition(self):
        added = fixture("1.0.0-rc.1+build.2", clinical=True)
        entry = json.loads(added.pop("index.json"))["releases"][0]
        self.proposed.update(added)
        self.index(lambda x: x["releases"].append(entry))
        self.assertEqual(len(guard.compare(self.base, self.proposed)), 2)

    def test_historical_policy_independence(self):
        # Later clinical resources, dependencies, and absence of scaffold examples
        # are intentionally irrelevant to historical identity and byte integrity.
        history = fixture(clinical=True)
        guard.compare(history, history)

    def test_unsafe_archives(self):
        for name, kind in [
            ("../bad", tarfile.REGTYPE),
            ("/bad", tarfile.REGTYPE),
            ("package/../bad", tarfile.REGTYPE),
            ("package/a\\b", tarfile.REGTYPE),
            ("package/link", tarfile.SYMTYPE),
            ("package/link", tarfile.LNKTYPE),
            ("package/pax", tarfile.XHDTYPE),
        ]:
            with self.subTest(name=name, kind=kind):
                self.proposed = copy.deepcopy(self.base)
                self.proposed[self.prefix + "package.tgz"] = pack([(name, b"", kind)])
                self.reseal()
                self.reject("Unsafe archive|Non-regular archive")

    def test_duplicate_archive_members(self):
        self.proposed[self.prefix + "package.tgz"] = pack(
            [("package/a", b"a", tarfile.REGTYPE)] * 2
        )
        self.reseal()
        self.reject("Duplicate archive member")

    def test_archive_trailing_data(self):
        with self.assertRaisesRegex(ValueError, "terminator"):
            guard.archive(
                gzip.compress(
                    gzip.decompress(self.base[self.prefix + "package.tgz"]) + b"bad"
                )
            )

    def test_duplicate_json_keys(self):
        self.proposed["index.json"] = b'{"packageId": "a", "packageId": "b"}'
        self.reject("Duplicate JSON field")

    def test_hash_field_tamper(self):
        manifest = json.loads(self.proposed[self.prefix + "manifest.json"])
        manifest["files"][self.prefix + "index.html"] = "0" * 64
        self.proposed[self.prefix + "manifest.json"] = encode(manifest)
        self.index(
            lambda x: x["releases"][0].update(
                manifestSha256=guard.digest(
                    self.proposed[self.prefix + "manifest.json"]
                )
            )
        )
        self.reject("File hash mismatch")

    def test_entry_value_type_change(self):
        old = json.loads(self.base["index.json"])
        old["releases"][0]["approved"] = True
        self.base["index.json"] = encode(old)
        old["releases"][0]["approved"] = 1
        self.proposed["index.json"] = encode(old)
        self.reject("Changed existing index entry")

    def test_malformed_manifest(self):
        self.proposed[self.prefix + "manifest.json"] = b"[]"
        self.index(
            lambda x: x["releases"][0].update(manifestSha256=guard.digest(b"[]"))
        )
        self.reject("Expected JSON object")

    def test_package_and_render_integrity(self):
        for member, key, value, expected in [
            ("package/package.json", "version", "9.9.9", "Package version mismatch"),
            ("package/" + guard.IG, "version", "9.9.9", "IG version mismatch"),
            (
                "package/other/spec.internals",
                "targets",
                ["missing.html"],
                "Missing render target",
            ),
        ]:
            with self.subTest(member=member):
                files = fixture("0.2.0")
                prefix = guard.RELEASES + "0.2.0/"
                entries = guard.archive(files[prefix + "package.tgz"])
                doc = json.loads(entries[member])
                doc[key] = value
                entries[member] = encode(doc)
                files[prefix + "package.tgz"] = pack(
                    [(n, b, tarfile.REGTYPE) for n, b in entries.items()]
                )
                entry = reseal(files, "0.2.0")
                files["index.json"] = encode(
                    {"packageId": guard.PACKAGE, "releases": [entry]}
                )
                with self.assertRaisesRegex(ValueError, expected):
                    guard.validate(files)

    def test_tar_checksum_and_truncation(self):
        data = bytearray(gzip.decompress(self.base[self.prefix + "package.tgz"]))
        data[0] = ord("X")
        with self.assertRaises(ValueError):
            guard.archive(gzip.compress(data))
        with self.assertRaises(ValueError):
            guard.archive(gzip.compress(bytes(data[:600])))

    def test_gitattributes_modification(self):
        self.base[".gitattributes"] = b"* -text\n"
        self.proposed[".gitattributes"] = b"* text=auto\n"
        self.reject(r"Modified historical file: \.gitattributes")

    def test_existing_top_level_index_field_change_or_removal(self):
        original = json.loads(self.base["index.json"])
        original["review"] = {"owner": "original", "approved": True}
        self.base["index.json"] = encode(original)
        for remove in [False, True]:
            with self.subTest(remove=remove):
                changed = copy.deepcopy(original)
                if remove:
                    del changed["review"]
                else:
                    changed["review"]["owner"] = "replacement"
                self.proposed["index.json"] = encode(changed)
                self.reject("index.json: changed field review")

    def test_unexpected_files_outside_releases(self):
        for name in ["unexpected.json", "ig/verification/extra.html"]:
            with self.subTest(path=name):
                self.proposed = {**self.base, name: b"unexpected"}
                self.reject("Unexpected history file: " + name)

    def test_renamed_release_with_consistent_resealed_history(self):
        renamed = "0.2.0"
        prefix = guard.RELEASES + renamed + "/"
        self.proposed = {
            prefix + name[len(self.prefix) :]: data
            for name, data in self.base.items()
            if name.startswith(self.prefix)
        }
        packed = guard.archive(self.proposed[prefix + "package.tgz"])
        for name in ["package/package.json", "package/" + guard.IG]:
            doc = json.loads(packed[name])
            doc["version"] = renamed
            if name == "package/package.json":
                doc["url"] = guard.CANONICAL + "/ig/verification/releases/" + renamed
            packed[name] = encode(doc)
        self.proposed[prefix + guard.IG] = packed["package/" + guard.IG]
        self.proposed[prefix + "package.tgz"] = pack(
            [(name, data, tarfile.REGTYPE) for name, data in packed.items()]
        )
        entry = reseal(self.proposed, renamed)
        self.proposed["index.json"] = encode(
            {"packageId": guard.PACKAGE, "releases": [entry]}
        )
        guard.validate(self.proposed)
        self.reject("Removed existing index entry: 0.1.0")

    def test_corrupted_compressed_stream_release_and_cli_error(self):
        # Valid gzip header followed by forbidden DEFLATE block type 3.
        broken = bytes.fromhex("1f8b080000000000000307") + bytes(8)
        with self.assertRaises(zlib.error):
            gzip.decompress(broken)
        self.proposed[self.prefix + "package.tgz"] = broken
        self.reseal()
        with self.assertRaisesRegex(
            ValueError, r"Release 0\.1\.0: .*invalid block type"
        ):
            guard.validate(self.proposed)
        stderr = io.StringIO()
        with (
            mock.patch("sys.argv", ["guard", "--base", "a" * 40, "--head", "b" * 40]),
            mock.patch.object(
                guard, "git_history", side_effect=[self.base, self.proposed]
            ),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            guard.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("Publication history rejected: Release 0.1.0:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_catches_unwrapped_zlib_error(self):
        stderr = io.StringIO()
        with (
            mock.patch("sys.argv", ["guard", "--base", "a" * 40, "--head", "b" * 40]),
            mock.patch.object(
                guard, "git_history", side_effect=zlib.error("compressed data corrupt")
            ),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            guard.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn(
            "Publication history rejected: compressed data corrupt", stderr.getvalue()
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_git_exact_base_and_outside_changes(self):
        with tempfile.TemporaryDirectory() as folder:

            def git(*args):
                return (
                    subprocess.check_output(
                        ["git", "-C", folder, *args], stderr=subprocess.STDOUT
                    )
                    .decode()
                    .strip()
                )

            git("init")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "core.autocrlf", "false")
            git("config", "core.hooksPath", str(Path(folder) / "no-hooks"))
            for name, data in self.base.items():
                p = Path(folder) / "publication-history" / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            git("add", ".")
            git("commit", "-m", "fixture base")
            base = git("rev-parse", "HEAD")
            (Path(folder) / "README.md").write_text("outside history")
            git("add", ".")
            git("commit", "-m", "outside change")
            head = git("rev-parse", "HEAD")
            guard.compare(
                guard.git_history(folder, base), guard.git_history(folder, head)
            )
            # Dirty files cannot substitute for the supplied exact commit.
            (Path(folder) / "publication-history/index.json").write_text("broken")
            guard.compare(
                guard.git_history(folder, base), guard.git_history(folder, head)
            )
            with self.assertRaisesRegex(ValueError, "exact 40-character"):
                guard.git_history(folder, "HEAD")


if __name__ == "__main__":
    unittest.main()
