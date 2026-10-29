#!/usr/bin/env python3
"""Verify Earthdata credentials without downloading a granule.

Mirrors the credential precedence in download_granules.py, then asks the
archive for the first byte of a real granule. Never prints the token itself.

    python check_auth.py
"""
import os
import sys
from pathlib import Path

import asf_search as asf

# Any granule from the download list; we only ever fetch one byte of it.
URL = ("https://nisar.asf.earthdatacloud.nasa.gov/NISAR/NISAR_L2_GCOV_PROVISIONAL_V1/"
       "NISAR_L2_PR_GCOV_017_012_D_071_4005_DHDH_A_20260404T010047_20260404T010122"
       "_P05023_N_F_J_001/NISAR_L2_PR_GCOV_017_012_D_071_4005_DHDH_A_20260404T010047"
       "_20260404T010122_P05023_N_F_J_001.h5")


def describe_token(t, pad=""):
    """Sanity-check the SHAPE of a token without revealing it.

    An Earthdata token is a JWT: three base64url segments separated by dots,
    beginning with 'eyJ' (base64 of '{"'). Anything else means the wrong text
    was copied -- the Generate Token page shows the expiry and other metadata
    around the token, and those are easy to grab by accident.
    """
    bad = []
    if t.startswith(("'", '"')) or t.endswith(("'", '"')):
        bad.append("wrapped in quotes")
    if any(c.isspace() for c in t):
        bad.append("contains whitespace")
    if not t.startswith("eyJ"):
        bad.append(f"does not start with 'eyJ' (starts {t[:4]!r})")
    if t.count(".") != 2:
        bad.append(f"has {t.count('.')} dots, a JWT has 2")
    if len(t) < 100:
        bad.append(f"only {len(t)} chars, tokens are typically 400+")
    if bad:
        print(f"{pad}LOOKS WRONG: " + "; ".join(bad))
    else:
        print(f"{pad}shape OK: JWT-like, {len(t)} chars")
    return not bad


def main():
    tok = os.environ.get("EARTHDATA_TOKEN")
    user, pw = os.environ.get("EARTHDATA_USER"), os.environ.get("EARTHDATA_PASS")
    netrc = Path.home() / ".netrc"
    tokfile = Path.home() / ".earthdata_token"

    print("credential sources visible to this shell:")
    print(f"  EARTHDATA_TOKEN env  : {'set, %d chars' % len(tok) if tok else 'not set'}")
    if tok:
        describe_token(tok, "        ")
    print(f"  EARTHDATA_USER/PASS  : {'set' if (user and pw) else 'not set'}")
    print(f"  ~/.earthdata_token   : ", end="")
    if tokfile.exists():
        mode = oct(tokfile.stat().st_mode & 0o777)
        raw = tokfile.read_text()
        n = len(raw.strip())
        warn = "  <-- too open, chmod 600" if tokfile.stat().st_mode & 0o077 else ""
        print(f"exists, {n} chars, mode {mode}{warn}")
        describe_token(raw.strip(), "        ")
        print("        (note: the sbatch loads this; an interactive shell does not)")
    else:
        print("absent")
    print(f"  ~/.netrc             : ", end="")
    if netrc.exists() and "urs.earthdata.nasa.gov" in netrc.read_text():
        mode = oct(netrc.stat().st_mode & 0o777)
        warn = "  <-- too open, chmod 600" if netrc.stat().st_mode & 0o077 else ""
        print(f"has an Earthdata entry, mode {mode}{warn}")
    else:
        print("no Earthdata entry")

    if tok:
        which, session = "EARTHDATA_TOKEN", asf.ASFSession().auth_with_token(tok)
    elif user and pw:
        which, session = "EARTHDATA_USER/PASS", asf.ASFSession().auth_with_creds(user, pw)
    elif netrc.exists():
        which, session = "~/.netrc", asf.ASFSession()
    else:
        sys.exit("\nNo credentials available. See download_granules.py header.")

    print(f"\nauthenticating with {which} ...")
    r = session.get(URL, stream=True, headers={"Range": "bytes=0-0"}, timeout=60)
    size = r.headers.get("Content-Range", "").split("/")[-1] or "?"
    body = len(next(r.iter_content(1), b""))
    r.close()

    print(f"  HTTP {r.status_code}  | granule size {size} bytes | got {body} byte(s)")
    if r.status_code in (200, 206) and body == 1:
        print("\nOK - credentials work and the archive is reachable.")
        return 0
    print("\nFAILED - 401/403 means bad or expired token (they last ~60 days); "
          "a redirect to a login page means the session did not authenticate.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

