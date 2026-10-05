import io
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from scripts.release_main import prepare, reserve_tag, publish, verify_public_image


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
        api.request.side_effect = [[ref(32, "new")], HTTPError("", 404, "missing", {}, None), {}]
        self.assertEqual(prepare(api, "new"), "v32")
        self.assertTrue(api.request.call_args.args[1]["draft"])
        api.request.side_effect = [[ref(32, "new")], {"id": 123}]
        self.assertEqual(prepare(api, "new"), "v32")

    def test_old_push_cannot_mark_its_release_latest(self):
        for head, expected in (("new", "true"), ("newer", "false")):
            api = Mock()
            api.request.side_effect = [ref(32, "new"), {"id": 123}, {"commit": {"sha": head}}, {}]
            publish(api, "v32", "new")
            self.assertEqual(api.request.call_args.args[1], {"draft": False, "make_latest": expected})

    def test_wrong_release_commit_is_refused(self):
        api = Mock()
        api.request.return_value = ref(32, "other")
        with self.assertRaises(RuntimeError):
            publish(api, "v32", "new")

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
