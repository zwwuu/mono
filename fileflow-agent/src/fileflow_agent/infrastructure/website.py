"""Infrastructure -- website upload (pickup mode).

POSTs a staged batch to the website as one multipart/form-data
request carrying every file. Auth, when configured, is a Bearer
token from an env var named in ``pickup.auth_env`` -- never from
the config file. A non-2xx response is a failure (the files stay
in outgoing/). For a single-file upload, when the site's JSON
reply carries a ``sha256``/``checksum`` or a ``size``, it is
compared with the uploaded file; a mismatch is a failure too. A
site that returns no checksum is accepted on 2xx. For a
multi-file batch the reply is only checked for 2xx -- the site
cannot report one checksum for many files.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.request
import uuid
from pathlib import Path

from fileflow_contract import sha256_file

from .. import __version__
from .config import DeliverConfig
from .filesystem import log


class WebsiteUploader:
    def __init__(self, cfg: DeliverConfig):
        self.url = cfg.upload_url
        self.field = cfg.field
        self.timeout = cfg.timeout_seconds
        self.auth_env = cfg.auth_env

    def upload(self, file_path: Path, *, run_id: str, job: str) -> None:
        self.upload_many([file_path], run_id=run_id, job=job)

    def upload_many(self, file_paths: list[Path], *, run_id: str, job: str) -> None:
        """One POST carrying every file, all under the configured field
        name. The batch is all or nothing: a failure leaves every file
        in outgoing/ for the next pickup pass."""
        file_paths = list(file_paths)
        if not file_paths:
            raise ValueError("upload_many called with no files")
        boundary = uuid.uuid4().hex
        body = bytearray()

        def field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(f"{value}\r\n".encode())

        field("run_id", run_id)
        field("job", job)
        if len(file_paths) == 1:
            # Single file: same request shape as before, including the
            # per-file checksum field the site may read.
            field("sha256", sha256_file(file_paths[0]))
        for file_path in file_paths:
            body.extend(f"--{boundary}\r\n".encode())
            ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            body.extend(
                f'Content-Disposition: form-data; name="{self.field}";'
                f' filename="{file_path.name}"\r\n'.encode()
            )
            body.extend(f"Content-Type: {ctype}\r\n\r\n".encode())
            body.extend(file_path.read_bytes())
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        headers = {
            "User-Agent": f"FileFlow-Agent/{__version__}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if self.auth_env and os.environ.get(self.auth_env):
            headers["Authorization"] = f"Bearer {os.environ[self.auth_env]}"
        req = urllib.request.Request(self.url, data=bytes(body), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp_body = r.read()
                status = r.status
        except OSError as e:
            names = ", ".join(p.name for p in file_paths)
            raise OSError(f"website upload failed for {names}: {e}") from e
        if not 200 <= status < 300:
            names = ", ".join(p.name for p in file_paths)
            raise OSError(f"website upload rejected {names}: HTTP {status}")
        if len(file_paths) == 1:
            # Single file: the reply can still vouch for the bytes.
            file_path = file_paths[0]
            file_sha = sha256_file(file_path)
            try:
                reply = json.loads(resp_body.decode("utf-8")) if resp_body else {}
            except (ValueError, UnicodeDecodeError) as _:
                reply = {}
            if isinstance(reply, dict):
                remote_sha = reply.get("sha256") or reply.get("checksum")
                if remote_sha and str(remote_sha).lower() != file_sha:
                    raise OSError(
                        f"website reported sha256 {remote_sha} for {file_path.name}, "
                        f"expected {file_sha}"
                    )
                if "size" in reply:
                    try:
                        remote_size = int(reply["size"])
                    except (TypeError, ValueError):
                        raise OSError(
                            f"website reported unparseable size {reply['size']!r} "
                            f"for {file_path.name}"
                        ) from None
                    if remote_size != file_path.stat().st_size:
                        raise OSError(f"website reported size {reply['size']} for {file_path.name}")
        log.info(
            "uploaded %d file(s) to website (run %s, HTTP %s)",
            len(file_paths),
            run_id,
            status,
        )
