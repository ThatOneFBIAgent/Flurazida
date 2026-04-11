# Hosting utility for large files

import aiohttp
import io
import os
from typing import Optional
from logging_modules.custom_logger import get_logger

log = get_logger()

LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"

async def upload_to_litterbox(data: bytes, filename: str, duration: str = "12h") -> Optional[str]:
    """
    Uploads a file to Litterbox for temporal hosting.
    duration can be: "1h", "12h", "24h", "72h"
    Returns the URL if successful, else None.
    """
    try:
        # We need to provide a filename to Litterbox for it to accept the upload properly
        form = aiohttp.FormData()
        form.add_field("reqtype", "fileupload")
        form.add_field("time", duration)
        form.add_field("fileToUpload", data, filename=filename)

        async with aiohttp.ClientSession() as session:
            async with session.post(LITTERBOX_API, data=form) as resp:
                if resp.status == 200:
                    url = await resp.text()
                    log.info(f"Uploaded {filename} to Litterbox ({duration}): {url}")
                    return url.strip()
                else:
                    text = await resp.text()
                    log.error(f"Litterbox upload failed ({resp.status}): {text}")
                    return None
    except Exception as e:
        log.exception(f"Litterbox upload error: {e}")
        return None
