#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright 2026 noknok (Christopher Houben)
"""dev_install.py - put a product that is NOT published yet onto YOUR OWN brain (DEV-259).

For makers testing their product before they open a pull request. The brain must already
be set up with the noknok app (any product) and be on your home WiFi. This sends the same
"product switch" the app sends: the brain downloads your product.py from the URL in your
manifest, checks that it compiles, and restarts into it. If the download or compile fails,
it keeps the product it had.

  python tools/dev_install.py --manifest my-light.json --brain 192.168.1.42
  python tools/dev_install.py --manifest https://raw.githubusercontent.com/<you>/poc/<branch>/manifests/my-light.json --brain noknok-ab12.local

The manifest's files[] product.py "url" must be a raw https URL the brain can reach (your
fork on GitHub, a branch, any static host). The validator (tools/validate_product.py) runs
first; with ERRORs nothing is sent (--skip-check only for your own experiments).

Standard library only. To go back: install a published product with the app (or run this
tool with that product's manifest from the poc repo).
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import validate_product as vp  # noqa: E402


def load_manifest(src):
    if src.startswith("https://") or src.startswith("http://"):
        with urllib.request.urlopen(src, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    with open(src, encoding="utf-8") as f:
        return json.load(f)


def fetch(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read()


def check(m, schema):
    """Validate the manifest + the script it points to (downloaded), like a folder."""
    d = tempfile.mkdtemp(prefix="noknok_dev_")
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f)
    for fe in m.get("files", []):
        if isinstance(fe, dict) and str(fe.get("dest", "")).endswith(".py") and fe.get("url"):
            with open(os.path.join(d, os.path.basename(fe["dest"])), "wb") as f:
                f.write(fetch(fe["url"]))
    if m.get("image"):
        try:
            with open(os.path.join(d, "image.webp"), "wb") as f:
                f.write(fetch(m["image"]))
        except Exception:
            pass
    rep = vp.validate_folder(d, schema)
    # A dev install needs no README / licence note; that is for publishing.
    rep.errors = [(c, msg) for c, msg in rep.errors if c != "licence"]
    return rep


def rpc(host, op, args, timeout=25):
    msg = {"id": 1, "op": op, "args": args}
    req = urllib.request.Request("http://%s/rpc" % host, data=json.dumps(msg).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install an unpublished product on your brain.")
    ap.add_argument("--manifest", required=True, help="manifest file or raw https URL")
    ap.add_argument("--brain", required=True, help="brain IP or noknok-XXXX.local")
    ap.add_argument("--schema", help="path to product-manifest.schema.json")
    ap.add_argument("--skip-check", action="store_true", help="do not run the validator")
    a = ap.parse_args(argv)

    m = load_manifest(a.manifest)
    script = next((f.get("url") for f in m.get("files", [])
                   if isinstance(f, dict) and f.get("dest") == "product.py"), None)
    if not script or not str(script).startswith("https://"):
        print("The manifest has no https url for product.py in files[].")
        return 2
    if not a.skip_check:
        rep = check(m, vp.load_schema(a.schema))
        rep.print()
        if rep.errors:
            print("\nNot installed: fix the ERRORs first (same check as a pull request).")
            return 1

    hello = rpc(a.brain, "hello", {}, timeout=8)
    if hello.get("ap"):
        print("The brain is in setup mode. Set it up with the app first (any product).")
        return 2
    print("Brain %s (%s) runs %s - switching to %s ..." % (
        hello.get("name"), a.brain, (hello.get("product") or {}).get("id"), m.get("id")))
    defaults = {f["id"]: f.get("default") for f in m.get("config_schema", [])
                if isinstance(f, dict) and f.get("id") and f.get("type") != "info"}
    reply = rpc(a.brain, "provision", {
        "script_url": script, "product_id": m.get("id", ""),
        "module_firmware": m.get("module_firmware", {}), "config_defaults": defaults})
    if not reply.get("accepted"):
        print("The brain refused: %s" % json.dumps(reply))
        return 1
    print("Accepted. The brain restarts, downloads your script and starts it (about a "
          "minute). If the script does not download or compile, it keeps the old product.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
