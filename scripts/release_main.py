"""Reserve one numbered release per main commit; retries reuse its tag."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class GitHub:
    def __init__(self, repository: str, token: str):
        self.repository = repository
        self.token = token

    def request(self, path: str, data=None, method=None):
        request = Request(f"https://api.github.com/repos/{self.repository}/{path}",
            data=None if data is None else json.dumps(data).encode(), method=method,
            headers={"Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "M3U-Web-Picker-Release"})
        with urlopen(request, timeout=60) as response:
            return json.load(response)


def numbered_tags(refs):
    return [(int(match[1]), ref) for ref in refs
            if (match := re.fullmatch(r"refs/tags/v(\d+)", ref["ref"]))]


def reserve_tag(api, sha):
    for _ in range(20):
        tags = numbered_tags(api.request("git/matching-refs/tags/v"))
        for number, ref in sorted(tags, reverse=True, key=lambda item: item[0]):
            target = ref["object"]
            while target["type"] == "tag":
                target = api.request(f"git/tags/{target['sha']}")["object"]
            if target["sha"] == sha:
                return f"v{number}"
        tag = f"v{max((number for number, _ in tags), default=31) + 1}"
        try:
            api.request("git/refs", {"ref": f"refs/tags/{tag}", "sha": sha})
            return tag
        except HTTPError as error:
            # Another main push can reserve this number concurrently. Verify
            # that collision before retrying; other validation errors must fail.
            if error.code != 422:
                raise
            try:
                api.request(f"git/ref/tags/{tag}")
            except HTTPError:
                raise error
    raise RuntimeError("Could not reserve a release number after concurrent pushes.")


def find_release(api, tag):
    try:
        return api.request(f"releases/tags/{tag}")
    except HTTPError as error:
        if error.code != 404:
            raise
    # The release-by-tag endpoint excludes drafts. The authenticated list
    # includes them, so failed runs can find and resume their draft release.
    page = 1
    while True:
        releases = api.request(f"releases?per_page=100&page={page}")
        for release in releases:
            if release["tag_name"] == tag:
                return release
        if len(releases) < 100:
            return None
        page += 1


def prepare(api, sha):
    tag = reserve_tag(api, sha)
    if find_release(api, tag) is None:
        api.request("releases", {"tag_name": tag, "target_commitish": sha,
            "name": tag, "draft": True, "generate_release_notes": True})
    return tag


def publish(api, tag, sha):
    ref = api.request(f"git/ref/tags/{tag}")
    if ref["object"]["sha"] != sha:
        raise RuntimeError("Release tag does not match the workflow commit.")
    release = find_release(api, tag)
    if release is None:
        raise RuntimeError("The reserved release was not found.")
    current = api.request("branches/main")["commit"]["sha"] == sha
    api.request(f"releases/{release['id']}", {"draft": False,
        "make_latest": "true" if current else "false"}, method="PATCH")


def verify_public_image(image, tag):
    if not image.startswith("ghcr.io/"):
        raise ValueError("Expected a GitHub Container Registry image.")
    name = image.removeprefix("ghcr.io/")
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{name}:pull"})
    # This token is anonymous, never the authenticated workflow credential.
    with urlopen(f"https://ghcr.io/token?{query}", timeout=30) as response:
        token = json.load(response)["token"]
    request = Request(f"https://ghcr.io/v2/{name}/manifests/{tag}", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json"})
    with urlopen(request, timeout=30) as response:
        manifest = json.load(response)
    platforms = {(item.get("platform", {}).get("os"), item.get("platform", {}).get("architecture"))
                 for item in manifest.get("manifests", [])}
    if not {("linux", "amd64"), ("linux", "arm64")} <= platforms:
        raise RuntimeError("The public image is missing a supported architecture.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "publish", "verify"))
    args = parser.parse_args()
    if args.command == "verify":
        verify_public_image(os.environ["IMAGE"], os.environ["TAG"])
        return
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    sha = os.environ["GITHUB_SHA"]
    if args.command == "prepare":
        tag = prepare(api, sha)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"tag={tag}\nimage=ghcr.io/{api.repository.lower()}\n")
        print(f"Reserved {tag} for {sha[:12]}")
    else:
        publish(api, os.environ["RELEASE_TAG"], sha)


if __name__ == "__main__":
    main()
