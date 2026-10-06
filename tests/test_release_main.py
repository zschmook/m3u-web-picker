import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from scripts.release_main import GitHub, prepare, reserve_tag, publish, upload_assets, verify_public_image, find_release


def ref(number, sha, kind="commit"):
    return {"ref": f"refs/tags/v{number}", "object": {"sha": sha, "type": kind}}


class ReleaseTests(unittest.TestCase):
    def test_first_new_release_ignores_preview_tags(self):
        api = Mock()
        api.request.side_effect = [[ref(31, "old"), {"ref": "refs/tags/v30-python-preview1"}], {}]
        self.assertEqual(reserve_tag(api, "new"), "v32")
        self.assertEqual(api.request.call_args.args[1], {"ref": "refs/tags/v32", "sha": "new"})

    def test_retry_reuses_existing_tag_including_annotated_tags(self):
        api = Mock()
        api.request.side_effect = [[ref(32, "annotated", "tag")], {"object": {"type": "commit", "sha": "new"}}]
        self.assertEqual(reserve_tag(api, "new"), "v32")
        self.assertEqual(api.request.call_count, 2)

    def test_concurrent_push_gets_next_number(self):
        api = Mock()
        collision = HTTPError("", 422, "conflict", {}, None)
        api.request.side_effect = [[ref(31, "old")], collision, ref(32, "other"),
            [ref(31, "old"), ref(32, "other")], {}]
        self.assertEqual(reserve_tag(api, "new"), "v33")

    def test_permission_errors_are_not_treated_as_tag_collisions(self):
        api = Mock()
        api.request.side_effect = [[ref(31, "old")], HTTPError("", 403, "denied", {}, None)]
        with self.assertRaises(HTTPError):
            reserve_tag(api, "new")

    def test_prepare_creates_draft_and_retry_preserves_it(self):
        api = Mock()
        api.request.side_effect = [[ref(32, "new")], HTTPError("", 404, "missing", {}, None), [], {}]
        self.assertEqual(prepare(api, "new"), "v32")
        self.assertTrue(api.request.call_args.args[1]["draft"])
        api.request.side_effect = [[ref(32, "new")], {"id": 123}]
        self.assertEqual(prepare(api, "new"), "v32")

    def test_old_push_cannot_mark_its_release_latest(self):
        for head, expected in (("new", "true"), ("newer", "false")):
            api = Mock()
            api.request.side_effect = [ref(32, "new"), {"id": 123}, {"commit": {"sha": head}}, {}]
            publish(api, "v32", "new")
            self.assertEqual(api.request.call_args.args[1], {"tag_name": "v32", "target_commitish": "new", "draft": False, "make_latest": expected})

    def test_draft_lookup_pages_through_authenticated_release_list(self):
        api = Mock()
        draft = {"id": 123, "tag_name": "v32", "draft": True}
        api.request.side_effect = [HTTPError("", 404, "draft hidden", {}, None),
            [{"tag_name": "unrelated"}] * 100, [draft]]
        self.assertEqual(find_release(api, "v32"), draft)
        self.assertEqual(api.request.call_args.args, ("releases?per_page=100&page=2",))

    def test_retry_finds_hidden_draft_without_creating_another_release(self):
        api = Mock()
        api.request.side_effect = [[ref(32, "new")], HTTPError("", 404, "draft hidden", {}, None),
            [{"id": 123, "tag_name": "v32", "draft": True}]]
        self.assertEqual(prepare(api, "new"), "v32")
        self.assertEqual(api.request.call_count, 3)

    def test_wrong_release_commit_is_refused(self):
        api = Mock()
        api.request.return_value = ref(32, "other")
        with self.assertRaises(RuntimeError):
            publish(api, "v32", "new")

    def asset_folder(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for name in ("docker-compose.release.yml", "docker-compose.gpu.yml", "docker-compose.discovery.yml", ".env.example"):
            (root / name).write_bytes((name + "\n").encode())
        return root

    def test_upload_finds_hidden_draft_and_uses_the_documented_environment_asset_name(self):
        root = self.asset_folder()
        api = Mock()
        api.request.side_effect = [ref(37, "new"), HTTPError("", 404, "hidden", {}, None),
                                  [{"id": 17, "tag_name": "v37", "draft": True, "assets": []}]]
        upload_assets(api, "v37", "new", root)
        self.assertEqual(api.upload_asset.call_count, 4)
        self.assertEqual(api.upload_asset.call_args.args, (17, "default.env.example", b".env.example\n"))
        self.assertEqual(api.request.call_args.args, ("releases?per_page=100&page=1",))

    def test_upload_retry_skips_identical_files_and_replaces_only_changed_managed_assets(self):
        root = self.asset_folder()
        assets = []
        for index, path in enumerate(root.iterdir()):
            name = "default.env.example" if path.name == ".env.example" else path.name
            assets.append(dict(id=index+1, name=name, state="uploaded", digest="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()))
        assets[0]["digest"] = "sha256:old"
        assets.append(dict(id=999, name="installer.exe", digest="untouched"))
        api = Mock()
        api.request.side_effect = [ref(37, "new"), {"id": 17, "assets": assets}, None]
        upload_assets(api, "v37", "new", root)
        self.assertEqual(api.upload_asset.call_count, 1)
        self.assertEqual(api.request.call_args.args, (f"releases/assets/{assets[0]['id']}",))
        self.assertEqual(api.request.call_args.kwargs, {"method": "DELETE"})

    def test_upload_reads_all_inputs_before_replacing_assets(self):
        root = self.asset_folder()
        (root / ".env.example").unlink()
        api = Mock()
        api.request.side_effect = [ref(37, "new"), {"id": 17, "assets": []}]
        with self.assertRaises(FileNotFoundError):
            upload_assets(api, "v37", "new", root)
        api.upload_asset.assert_not_called()
        self.assertEqual(api.request.call_count, 2)

    def test_upload_refuses_a_tag_pointing_at_another_commit(self):
        api = Mock()
        api.request.return_value = ref(37, "other")
        with self.assertRaises(RuntimeError):
            upload_assets(api, "v37", "new")
        api.upload_asset.assert_not_called()
        self.assertEqual(api.request.call_count, 1)

    def test_asset_upload_sends_binary_bytes_to_github_uploads_with_a_safe_name(self):
        reply = io.BytesIO(b'{"id": 17}')
        with patch("scripts.release_main.urlopen", return_value=reply) as opened:
            GitHub("owner/repo", "test-token").upload_asset(12, "file name.yml", b"binary\x00data")
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://uploads.github.com/repos/owner/repo/releases/12/assets?name=file+name.yml")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"binary\x00data")
        self.assertEqual(request.get_header("Content-type"), "application/octet-stream")

    def test_delete_accepts_an_empty_204_response(self):
        reply = io.BytesIO(b"")
        reply.status = 204
        with patch("scripts.release_main.urlopen", return_value=reply):
            self.assertIsNone(GitHub("owner/repo", "test-token").request("releases/assets/7", method="DELETE"))

    def test_public_check_uses_anonymous_token_and_requires_both_platforms(self):
        for architectures, passes in ((["amd64", "arm64"], True), (["amd64"], False)):
            replies = [io.BytesIO(json.dumps({"token": "anonymous"}).encode()),
                io.BytesIO(json.dumps({"manifests": [{"platform": {"os": "linux", "architecture": arch}}
                    for arch in architectures]}).encode())]
            with patch("scripts.release_main.urlopen", side_effect=replies) as opened:
                if passes:
                    verify_public_image("ghcr.io/owner/image", "v32")
                else:
                    with self.assertRaises(RuntimeError):
                        verify_public_image("ghcr.io/owner/image", "v32")
                self.assertIsInstance(opened.call_args_list[0].args[0], str)
                self.assertEqual(opened.call_args.args[0].get_header("Authorization"), "Bearer anonymous")


if __name__ == "__main__":
    unittest.main()
