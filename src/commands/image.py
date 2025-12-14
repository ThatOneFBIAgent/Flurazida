
# Standard Library Imports
import asyncio
import io
import json
import logging
import math
import os
import re
import time
import zipfile
import urllib.parse
import tempfile
import subprocess
from typing import Dict, Optional, Tuple, List, Literal


# Third-Party Imports
import aiohttp
import discord
import numpy as np
import qrcode
from PIL import (
    Image, ImageDraw, ImageFont, ImageEnhance, ImageSequence, ImageFilter, ImageOps
)
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
try:
    from pyzbar.pyzbar import decode
    ZBAR_AVAILABLE = True
except ImportError:
    ZBAR_AVAILABLE = False
    decode = None  # Prevent NameError if accidentally called


# Local Imports
from config import cooldown
from logger import get_logger


log = get_logger()

# Global image selection memory
USER_SELECTED: Dict[int, Tuple[str, float]] = {}

# Import config from extraconfig
from extraconfig import EXT_BLACKLIST, MAX_JPEG_RECURSIONS, MAX_JPEG_QUALITY

# View for selecting an image from multiple attachments
class ImageSelectView(View):
    def __init__(self, interaction: discord.Interaction, attachments: list[discord.Attachment]):
        super().__init__(timeout=30)
        self.interaction = interaction
        self.attachments = attachments
        self.selected_url = None
        self.selected_index = None

        for i, att in enumerate(attachments[:5]):  # only show up to 5 buttons to avoid clutter - 25/10/25 ermm arent there up to 10 attachments? i mean atp just download them all lol
            button = Button(label=f"Image {i+1}", custom_id=f"img_{i}")
            async def button_callback(inter, index=i, att=att):
                # Only allow the original user to press these buttons
                if inter.user.id != self.interaction.user.id:
                    await inter.response.send_message("🚫 Not your selection!", ephemeral=True)
                    return

                # store selection for 30 minutes
                USER_SELECTED[self.interaction.user.id] = (att.url, time.time() + 30 * 60)

                self.selected_url = att.url
                self.selected_index = index + 1
                # Update the ephemeral message to confirm and remove buttons
                await inter.response.edit_message(
                    content=f"✅ Image #{index+1} selected ({att.filename})",
                    view=None
                )
                self.stop()
            button.callback = button_callback
            self.add_item(button)

        # Add cancel button
        cancel = Button(label="Cancel", style=discord.ButtonStyle.danger)
        async def cancel_callback(inter):
            if inter.user.id != self.interaction.user.id:
                await inter.response.send_message("🚫 Not your selection!", ephemeral=True)
                return
            await inter.response.edit_message(content="❌ Selection canceled.", view=None)
            self.stop()
        cancel.callback = cancel_callback
        self.add_item(cancel)

@app_commands.context_menu(name="Select image")
async def select_image(interaction: discord.Interaction, message: discord.Message):
    """Context menu: choose an image/gif/link from a message and store it for 30 minutes."""
    await interaction.response.defer(ephemeral=True)

    valid_attachments: List[Tuple[str, str]] = []

    # 1) Direct attachments (Discord-hosted) - highest priority
    for a in message.attachments:
        if a.filename and a.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            valid_attachments.append((a.url, a.filename))

    # 2) Embeds (images/thumbnails/provider url)
    for e in message.embeds:
        # embed.image
        if getattr(e, "image", None) and getattr(e.image, "url", None):
            url = e.image.url
            valid_attachments.append((url, os.path.basename(urllib.parse.urlparse(url).path) or url))
        # embed.thumbnail
        if getattr(e, "thumbnail", None) and getattr(e.thumbnail, "url", None):
            url = e.thumbnail.url
            valid_attachments.append((url, os.path.basename(urllib.parse.urlparse(url).path) or url))
        # embed.url (sometimes direct link to media)
        if getattr(e, "url", None):
            url = e.url
            if url.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                valid_attachments.append((url, os.path.basename(urllib.parse.urlparse(url).path) or url))

    # 3) URLs in message content
    url_pattern = r"(https?://[^\s<>]+)"
    found_links = re.findall(url_pattern, message.content or "")

    async def resolve_media_url(session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """Return a direct media URL (or same URL) if it appears to be image/video by checking headers or Tenor fallback."""
        try:
            # Try HEAD first to pick up Content-Type without downloading content
            async with session.head(url, allow_redirects=True, timeout=6) as h:
                ctype = h.headers.get("Content-Type", "").lower()
                if ctype.startswith("image/") or "gif" in ctype or "webp" in ctype or ctype.startswith("video/"):
                    return str(h.url)
                # Some hosts don't return good Content-Type on HEAD (or disallow HEAD), fall through
        except Exception:
            # HEAD can fail — fallback to small GET
            pass

        # GET a tiny range to get headers and small body
        try:
            headers = {"Range": "bytes=0-8191"}  # small chunk to avoid full download
            async with session.get(url, allow_redirects=True, headers=headers, timeout=8) as g:
                ctype = g.headers.get("Content-Type", "").lower()
                final = str(g.url)
                # Accept if server says image or video
                if ctype.startswith("image/") or "gif" in ctype or "webp" in ctype or ctype.startswith("video/"):
                    return final

                # If content-type absent or generic, try to sniff URL extension
                path = urllib.parse.urlparse(final).path.lower()
                if any(path.endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4', '.webm')):
                    return final

                # Tenor special: page HTML sometimes; try to extract JSON blob for main media
                if "tenor.com" in final:
                    text = await g.text()
                    # Try __NEXT_DATA__ JSON first (better)
                    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.DOTALL)
                    if m:
                        try:
                            data = json.loads(m.group(1))
                            # dig for main media URL
                            media = None
                            # structure can vary; try common paths
                            media = (
                                data.get("props", {})
                                    .get("pageProps", {})
                                    .get("post", {})
                                    .get("media")
                            )
                            if media and isinstance(media, list) and media:
                                # pick the first media item (main gif)
                                main = media[0]
                                # many entries have gif/mediumgif/mp4 keys
                                for key in ("gif", "mediumgif", "mp4", "preview", "tinygif"):
                                    if isinstance(main.get(key), dict) and main[key].get("url"):
                                        return main[key]["url"]
                                # sometimes it's a URL string
                                for key in ("url",):
                                    if main.get(key):
                                        return main.get(key)
                        except Exception:
                            pass

                    # fallback: parse og:image meta
                    m2 = re.search(r'<meta[^>]+property=["\']og:(?:image|video)["\'][^>]+content=["\']([^"\']+)["\']', text)
                    if m2:
                        return m2.group(1)
                # Giphy or other services might have similar embedded media URLs in HTML; we could add more parsers here
        except Exception:
            pass

        return None

    # Use shared session to resolve found links
    session = interaction.client.http_session
    if session:
        for link in found_links:
            # skip obvious non-media shorteners / trackers quickly
            try:
                resolved = await resolve_media_url(session, link)
                if resolved:
                    fname = os.path.basename(urllib.parse.urlparse(resolved).path) or resolved
                    valid_attachments.append((resolved, fname))
            except Exception:
                # ignore per-url failures
                pass

    # Remove duplicates preserving order
    seen = set()
    filtered = []
    for url, fname in valid_attachments:
        if url in seen:
            continue
        seen.add(url)
        filtered.append((url, fname))
    valid_attachments = filtered

    if not valid_attachments:
        await interaction.followup.send("❌ No valid images or GIFs found in that message.", ephemeral=True)
        return None, None

    # If single result, auto-select
    if len(valid_attachments) == 1:
        url, fname = valid_attachments[0]
        USER_SELECTED[interaction.user.id] = (url, time.time() + 30 * 60)
        await interaction.followup.send(f"✅ Image selected automatically: `{fname}`", ephemeral=True)
        return url, fname

    # Multiple — create UI for selecting among urls (up to 5)
    class ImageSelectView(View):
        def __init__(self, interaction: discord.Interaction, attachments: List[Tuple[str,str]]):
            super().__init__(timeout=60)
            self.interaction = interaction
            self.attachments = attachments
            self.selected_url = None
            self.selected_index = None

            for idx, (url, fname) in enumerate(attachments[:5], start=1):
                btn = Button(label=f"{idx}: {fname[:40]}", custom_id=f"img_{idx}")
                async def cb(i, b_url=url, b_idx=idx, b_fname=fname):
                    def _cb(inter: discord.Interaction):
                        return None
                    return None
                # create proper closure callback
                async def make_cb(inter, b_url=url, b_idx=idx, b_fname=fname):
                    if inter.user.id != self.interaction.user.id:
                        await inter.response.send_message("🚫 Not your selection!", ephemeral=True)
                        return
                    USER_SELECTED[self.interaction.user.id] = (b_url, time.time() + 30 * 60)
                    self.selected_url = b_url
                    self.selected_index = b_idx
                    await inter.response.edit_message(content=f"✅ Image #{b_idx} selected ({b_fname})", view=None)
                    self.stop()
                btn.callback = make_cb
                self.add_item(btn)

            cancel = Button(label="Cancel", style=discord.ButtonStyle.danger)
            async def cancel_cb(inter):
                if inter.user.id != self.interaction.user.id:
                    await inter.response.send_message("🚫 Not your selection!", ephemeral=True)
                    return
                await inter.response.edit_message(content="❌ Selection canceled.", view=None)
                self.stop()
            cancel.callback = cancel_cb
            self.add_item(cancel)

    view = ImageSelectView(interaction, valid_attachments)
    await interaction.followup.send("🖼️ Multiple images found — pick one:", view=view, ephemeral=True)
    await view.wait()

    if not view.selected_url:
        await interaction.followup.send("❌ No image selected (timed out or cancelled).", ephemeral=True)
        return None, None

    return view.selected_url, valid_attachments[view.selected_index - 1][1]


class ImageCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="image", description="Image manipulation commands")
        self.bot = bot

    # Selection / helpers
    def _get_user_selection(self, user_id: int) -> Optional[str]:
        entry = USER_SELECTED.get(user_id)
        if not entry:
            return None
        url, expiry = entry
        if time.time() > expiry:
            del USER_SELECTED[user_id]
            return None
        return url

    async def _fetch_bytes(self, attachment: Optional[discord.Attachment], url: Optional[str]) -> Optional[bytes]:
        """Fetch bytes from either an attachment or URL."""
        if attachment:
            try:
                return await attachment.read()
            except Exception as e:
                log.exception("Failed to read attachment: %s", e)
                return None
        if not url:
            return None
        session = self.bot.http_session
        if not session:
            log.error("HTTP session not available")
            return None
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
        except Exception as e:
            log.exception("Failed to fetch URL: %s", e)
            return None

    def _load_frames_from_bytes(self, data: bytes) -> Tuple[List[Image.Image], int]:
        """Return list of PIL frames and a default duration (ms)."""
        bio = io.BytesIO(data)
        im = Image.open(bio)
        frames: List[Image.Image] = []
        duration = 80
        try:
            if getattr(im, "is_animated", False):
                for frame in ImageSequence.Iterator(im):
                    frames.append(frame.convert("RGBA"))
                # try to get duration from original
                try:
                    duration = im.info.get("duration", duration)
                except Exception:
                    pass
            else:
                frames = [im.convert("RGBA")]
        except Exception:
            # fallback single frame
            frames = [im.convert("RGBA")]
        return frames, duration

    def _frames_to_gif_bytes(self, frames: List[Image.Image], duration_ms: int = 80, loop: int = 0) -> bytes:
        """Save frames to GIF bytes (PIL save_all)."""
        bio = io.BytesIO()
        if len(frames) == 1:
            # Convert to RGB first, then quantize to ensure proper color handling
            frame = frames[0]
            if frame.mode == 'RGBA':
                # Create white background for transparency
                rgb = Image.new('RGB', frame.size, (255, 255, 255))
                rgb.paste(frame, mask=frame.split()[3])  # Use alpha channel as mask
                frame = rgb
            elif frame.mode != 'RGB':
                frame = frame.convert('RGB')
            # Quantize with dither=0 to preserve white
            quantized = frame.quantize(colors=256, dither=Image.Dither.NONE)
            quantized.save(bio, format="GIF")
        else:
            first, rest = frames[0], frames[1:]
            # Convert all frames to RGB with white background for transparency
            processed_frames = []
            for f in [first] + rest:
                if f.mode == 'RGBA':
                    rgb = Image.new('RGB', f.size, (255, 255, 255))
                    rgb.paste(f, mask=f.split()[3])
                    f = rgb
                elif f.mode != 'RGB':
                    f = f.convert('RGB')
                processed_frames.append(f)
            
            # Quantize first frame and use it as palette for others
            first_quantized = processed_frames[0].quantize(colors=256, dither=Image.Dither.NONE)
            rest_quantized = [f.quantize(colors=256, palette=first_quantized, dither=Image.Dither.NONE) for f in processed_frames[1:]]
            
            first_quantized.save(
                bio,
                format="GIF",
                save_all=True,
                append_images=rest_quantized,
                loop=loop,
                duration=duration_ms,
                disposal=2,
            )
        bio.seek(0)
        return bio.read()

    async def _send_image_bytes(self, interaction: discord.Interaction, data: bytes, filename: str, ephemeral: bool = False):
        """Helper to reply with a file and size info."""
        size_mb = len(data) / (1024 * 1024)
        size_str = f"{size_mb:.1f} MB" if size_mb >= 1 else f"{size_mb * 1024:.0f} KB"

        file = discord.File(io.BytesIO(data), filename=filename)
        await interaction.followup.send(
            content=f"📎 **{filename}** · {size_str}",
            file=file,
            ephemeral=ephemeral
        )
   
    async def _video_to_gif_bytes(self, data: bytes):
        """Convert video bytes to GIF bytes, hard-limiting to 10s before decode."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_in:
            tmp_in.write(data)
            tmp_in.flush()
            gif_path = tmp_in.name.replace(".mp4", ".gif")

            # Use ffmpeg directly to cut and convert only first 10s
            subprocess.run([
                "ffmpeg",
                "-y",                # overwrite
                "-t", "10",          # hard limit to 10 seconds
                "-i", tmp_in.name,
                "-vf", "fps=15,scale=320:-1:flags=lanczos",  # reasonable quality
                "-loop", "0",
                gif_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            with open(gif_path, "rb") as f:
                gif_data = f.read()

        os.remove(tmp_in.name)
        os.remove(gif_path)
        return gif_data

    # Utility transforms

    def wrap_text(self, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        lines = []
        for word in text.split():
            # break long words
            while font.getlength(word) > max_width:
                for i in range(1, len(word)+1):
                    if font.getlength(word[:i]) > max_width:
                        lines.append(word[:i-1])
                        word = word[i-1:]
                        break
            lines.append(word)

        wrapped_lines = []
        current_line = ""
        for word in lines:
            test_line = f"{current_line} {word}".strip() if current_line else word
            if font.getlength(test_line) <= max_width:
                current_line = test_line
            else:
                if current_line:
                    wrapped_lines.append(current_line)
                current_line = word
        if current_line:
            wrapped_lines.append(current_line)
        return wrapped_lines

    def _draw_text_centered(
        self,
        img: Image.Image,
        text: str,
        *,
        bottom: bool = False,
        font_path: str = os.path.join(os.getcwd(), "resources", "impact.ttf"),
        max_font_size: int = 64,
        padding: int = 10,
        bg_color: Tuple[int,int,int,int] = (255, 255, 255, 255)
    ) -> Image.Image:

        # work in RGB only to avoid GIF palette corruption later, UNLESS we want to preserve alpha for PNG
        if img.mode != "RGBA" and img.mode != "RGB":
             img = img.convert("RGB")

        w, h = img.size
        font_size = max_font_size
        wrapped_lines = []

        # shrink until it fits
        while font_size > 6:
            font = ImageFont.truetype(font_path, font_size)
            lines = []
            for para in text.split("\n"):
                lines.extend(self.wrap_text(para, font, w - 2 * padding))
            lh = font.getbbox("Ay")[3]
            box_h = len(lines) * lh + 2 * padding
            if box_h <= h // 2:
                wrapped_lines = lines
                break
            font_size -= 2

        if not wrapped_lines:
            font = ImageFont.truetype(font_path, font_size)
            wrapped_lines = self.wrap_text(text, font, w - 2 * padding)
            lh = font.getbbox("Ay")[3]
            box_h = len(wrapped_lines) * lh + 2 * padding

        # make new RGB canvas
        new_h = h + box_h
        new_img = Image.new("RGB", (w, new_h), (255, 255, 255))

        if bottom:
            new_img.paste(img, (0, 0))
            box_y = h
        else:
            new_img.paste(img, (0, box_h))
            box_y = 0

        draw = ImageDraw.Draw(new_img)
        draw.rectangle([0, box_y, w, box_y + box_h], fill=(255,255,255))

        # centered text
        for i, line in enumerate(wrapped_lines):
            tw, th = draw.textbbox((0,0), line, font=font)[2:]
            tx = (w - tw) // 2
            ty = box_y + padding + i * lh
            draw.text((tx, ty), line, font=font, fill=(0, 0, 0))

        return new_img

    def _flip_frame(self, frame: Image.Image, axis: Literal["horizontal", "vertical", "both"]) -> Image.Image:
        if axis == "horizontal":
            return frame.transpose(Image.FLIP_LEFT_RIGHT)
        elif axis == "vertical":
            return frame.transpose(Image.FLIP_TOP_BOTTOM)
        else:
            # both
            return frame.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM)

    def _jpegify_bytes(self, frames: List[Image.Image], recursions: int = 1, quality: int = 20) -> List[Image.Image]:
        """Apply jpeg artifact recursion to each frame. Returns frames (RGBA)."""
        out_frames = []
        for frame in frames:
            img = frame.convert("RGB")  # JPEG doesn't support alpha
            for _ in range(max(1, recursions)):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                buf.seek(0)
                img = Image.open(buf).convert("RGB")
            out_frames.append(img.convert("RGBA"))
        return out_frames

    def _resize_if_needed(self, frames: List[Image.Image], max_dim: int = 900) -> List[Image.Image]:
        """Resize frames so largest side <= max_dim to avoid massive processing."""
        w, h = frames[0].size
        max_side = max(w, h)
        if max_side <= max_dim:
            return frames
        ratio = max_dim / max_side
        new_size = (int(w * ratio), int(h * ratio))
        resized = [f.resize(new_size, Image.LANCZOS) for f in frames]
        return resized


    # Commands

    async def _resolve_image_bytes(
        self,
        interaction: discord.Interaction,
        attachment: Optional[discord.Attachment],
        image_url: Optional[str],
    ) -> Optional[bytes]:
        """Combine sources: explicit attachment -> explicit url -> user's selection -> referenced message attachment."""
        # 1) explicit attachment
        if attachment:
            b = await self._fetch_bytes(attachment, None)
            if b:
                return b

        # 2) explicit url
        if image_url:
            b = await self._fetch_bytes(None, image_url)
            if b:
                return b

        # 3) user selection
        sel = self._get_user_selection(interaction.user.id)
        if sel:
            b = await self._fetch_bytes(None, sel)
            if b:
                return b

        # 4) message reference (if present)
        msg = getattr(interaction, "message", None)
        if msg and getattr(msg, "reference", None):
            try:
                ref_msg = await interaction.channel.fetch_message(msg.reference.message_id)
                if ref_msg.attachments:
                    return await self._fetch_bytes(ref_msg.attachments[0], None)
            except Exception:
                pass

        return None

    @app_commands.command(name="forcegif", description="Convert an image (or gif) to a forced GIF output.")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def force_gif(
        self,
        interaction: discord.Interaction,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        log.info(f"ForceGIF invoked by {interaction.user.id}")
        await interaction.response.defer()
        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"ForceGIF invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"ForceGIF invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")        
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"ForceGIF no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        # make at least 2 identical frames for better autoplay behavior
        if len(frames) == 1:
            frames = frames * 2
        # ensure not huge
        frames = self._resize_if_needed(frames, max_dim=900)
        gif = self._frames_to_gif_bytes(frames, duration_ms=duration)
        log.successtrace(f"ForceGIF success for {interaction.user.id}")
        await self._send_image_bytes(interaction, gif, "forced.gif")

    @app_commands.command(name="caption", description="Add a caption at the top or bottom of an image (accepts gifs).")
    @cooldown(cl=15, tm=30.0, ft=3)
    async def caption_image(
        self,
        interaction: discord.Interaction,
        caption: str,
        bottom: bool = False,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        log.info(f"Caption invoked by {interaction.user.id}: {caption}")
        await interaction.response.defer()
        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"Caption invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"Caption invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")        
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Caption no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=900)

        out_frames = []
        font_path = os.path.join(os.getcwd(), "resources", "impact.ttf")

        for f in frames:
            f = f.convert("RGBA")

            # dynamic font scaling — ensures text fits width nicely
            font_size = 46
            draw = ImageDraw.Draw(f)
            font = ImageFont.truetype(font_path, font_size)
            bbox = draw.textbbox((0, 0), caption, font=font)
            text_w = bbox[2] - bbox[0]

            while text_w > f.width - 40 and font_size > 24:
                font_size -= 2
                font = ImageFont.truetype(font_path, font_size)
                bbox = draw.textbbox((0, 0), caption, font=font)
                text_w = bbox[2] - bbox[0]

            # add caption properly
            framed = self._draw_text_centered(
                f,
                caption,
                bottom=bottom,
                font_path=font_path,
                max_font_size=font_size,
            )

            # Convert to RGBA to preserve white background properly
            # Use quantize with dither=0 to ensure white stays white
            rgba = framed.convert('RGBA')
            out_frames.append(rgba)

        # --- Save and send ---
        if len(frames) == 1:
            # Static image -> PNG
            # We already have the single frame in 'out_frames[0]'
            final_img = out_frames[0]
            bio = io.BytesIO()
            final_img.save(bio, format="PNG")
            bio.seek(0)
            await self._send_image_bytes(interaction, bio.read(), "captioned.png")
            log.successtrace(f"Caption success (static) for {interaction.user.id}")
        else:
            # Animated -> GIF
            gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
            await self._send_image_bytes(interaction, gif, "captioned.gif")
            log.successtrace(f"Caption success (gif) for {interaction.user.id}")

    @app_commands.command(name="jpegify", description="Apply JPEG artifacting. Set recursions to repeat the effect.")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def jpegify(
        self,
        interaction: discord.Interaction,
        recursions: int = 1,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):

        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"Jpegify invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"Jpegify invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")

        if recursions > MAX_JPEG_RECURSIONS:
            log.warningtrace(f"Jpegify recursion limit exceeded by {interaction.user.id}: {recursions}")
            return await interaction.response.send_message(f"❌ Recursions too high! Max is {MAX_JPEG_RECURSIONS}.", ephemeral=True)
        
        
        log.info(f"Jpegify invoked by {interaction.user.id} (recursions: {recursions})")
        await interaction.response.defer()
        recursions = max(1, min(25, recursions))
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Jpegify no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=900)
        out_frames = self._jpegify_bytes(frames, recursions=recursions, quality=18)
        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        log.successtrace(f"Jpegify success for {interaction.user.id} (x{recursions})")
        await self._send_image_bytes(interaction, gif, f"jpegified_x{recursions}.gif")

    @app_commands.command(name="avatar", description="Get a user's avatar (or your own by default).")
    @cooldown(cl=5, tm=25.0, ft=3)
    async def avatar(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ):
        log.info(f"Avatar invoked by {interaction.user.id}")
        await interaction.response.defer()
        target = user or interaction.user
        url = target.display_avatar.replace(size=1024).url
        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for avatar command")
            return await interaction.followup.send("❌ HTTP session not available.")
        async with session.get(url) as r:
            if r.status != 200:
                log.error(f"Avatar fetch failed: {r.status}")
                return await interaction.followup.send("❌ Failed to fetch avatar.")
            data = await r.read()
        log.successtrace(f"Avatar fetched for {interaction.user.id} (target: {target.id})")
        await self._send_image_bytes(interaction, data, f"{target.id}_avatar.png")

    @app_commands.command(name="banner", description="Get a user's banner (or your own by default).")
    @cooldown(cl=5, tm=25.0, ft=3)
    async def banner(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ):
        log.info(f"Banner invoked by {interaction.user.id}")
        await interaction.response.defer()
        target = user or interaction.user
        banner = target.banner
        if not banner:
            log.warningtrace(f"No banner found for {target.id}")
            return await interaction.followup.send("❌ This user has no banner.", ephemeral=True)
        url = banner.replace(size=1024).url
        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for banner command")
            return await interaction.followup.send("❌ HTTP session not available.")
        async with session.get(url) as r:
            if r.status != 200:
                log.error(f"Banner fetch failed: {r.status}")
                return await interaction.followup.send("❌ Failed to fetch banner.")
            data = await r.read()
        log.successtrace(f"Banner fetched for {interaction.user.id} (target: {target.id})")
        await self._send_image_bytes(interaction, data, f"{target.id}_banner.png")

    @app_commands.command(name="serverbanner", description="Get the server (guild) banner.")
    @cooldown(cl=5, tm=25.0, ft=3)
    async def serverbanner(self, interaction: discord.Interaction):
        log.info(f"ServerBanner invoked by {interaction.user.id}")
        await interaction.response.defer()
        if not interaction.guild:
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)
        banner = interaction.guild.banner
        if not banner:
            log.warningtrace(f"No server banner found for {interaction.guild.id}")
            return await interaction.followup.send("❌ This server has no banner.", ephemeral=True)
        url = interaction.guild.banner.replace(size=1024).url
        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for serverbanner command")
            return await interaction.followup.send("❌ HTTP session not available.")
        async with session.get(url) as r:
            if r.status != 200:
                log.error(f"Server banner fetch failed: {r.status}")
                return await interaction.followup.send("❌ Failed to fetch server banner.")
            data = await r.read()
        log.successtrace(f"Server banner fetched for {interaction.user.id} (guild: {interaction.guild.id})")
        await self._send_image_bytes(interaction, data, f"{interaction.guild.id}_banner.png")

    @app_commands.command(name="emote", description="Gets raw emote image by its name.")
    @cooldown(cl=5, tm=25.0, ft=3)
    async def emote(
        self,
        interaction: discord.Interaction,
        emote_name: str,
    ):
        log.info(f"Emote invoked by {interaction.user.id}: {emote_name}")
        await interaction.response.defer()
        if not interaction.guild:
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)
        emote = discord.utils.get(interaction.guild.emojis, name=emote_name)
        if not emote:
            log.warningtrace(f"Emote not found: {emote_name}")
            return await interaction.followup.send(f"❌ No emote named '{emote_name}' found in this server.", ephemeral=True)

        url = emote.url.with_size(1024)
        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for emote command")
            return await interaction.followup.send("❌ HTTP session not available.")
        async with session.get(str(url)) as r:
            if r.status != 200:
                log.error(f"Emote fetch failed: {r.status}")
                return await interaction.followup.send("❌ Failed to fetch emote image.")
            data = await r.read()

        log.successtrace(f"Emote fetched for {interaction.user.id}: {emote_name}")
        ext = "gif" if emote.animated else "png"
        await self._send_image_bytes(interaction, data, f"{emote.id}_emote.{ext}")

    @app_commands.command(name="serveravatar", description="Get the server (guild) icon.")
    @cooldown(cl=5, tm=25.0, ft=3)
    async def serveravatar(self, interaction: discord.Interaction):
        log.info(f"ServerAvatar invoked by {interaction.user.id}")
        await interaction.response.defer()
        if not interaction.guild:
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)
        icon = interaction.guild.icon
        if not icon:
            log.warningtrace(f"No server icon found for {interaction.guild.id}")
            return await interaction.followup.send("❌ This server has no icon.", ephemeral=True)
        url = interaction.guild.icon.replace(size=1024).url
        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for serveravatar command")
            return await interaction.followup.send("❌ HTTP session not available.")
        async with session.get(url) as r:
            if r.status != 200:
                log.error(f"Server icon fetch failed: {r.status}")
                return await interaction.followup.send("❌ Failed to fetch server icon.")
            data = await r.read()
        log.successtrace(f"Server icon fetched for {interaction.user.id} (guild: {interaction.guild.id})")
        await self._send_image_bytes(interaction, data, f"{interaction.guild.id}_icon.png")

    @app_commands.command(name="flip", description="Flip an image horizontally/vertically or both.")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def flip(
        self,
        interaction: discord.Interaction,
        axis: Literal["horizontal", "vertical", "both"] = "horizontal",
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"Flip invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"Flip invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")        
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Flip no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)
        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=1200)
        out = [self._flip_frame(f, axis) for f in frames]
        gif = self._frames_to_gif_bytes(out, duration_ms=duration)
        log.successtrace(f"Flip success for {interaction.user.id} (axis: {axis})")
        await self._send_image_bytes(interaction, gif, f"flipped_{axis}.gif")

    # Globe effect

    def _sphere_project_frame(self, src: Image.Image, phase: float, out_size: Tuple[int, int]) -> Image.Image:
        """Map equirectangular src onto a sphere and return RGBA frame for given phase (radians)."""
        # ensure src is equirectangular (width is 2x height ideally). We'll sample using lon/lat mapping.
        src_w, src_h = src.size
        w, h = out_size
        src_np = np.array(src.convert("RGBA"))
        dst = np.zeros((h, w, 4), dtype=np.uint8)

        cx = w / 2.0
        cy = h / 2.0
        rx = w / 2.0
        ry = h / 2.0

        for y in range(h):
            ny = (y - cy) / ry  # -1 .. 1
            for x in range(w):
                nx = (x - cx) / rx  # -1 .. 1
                r2 = nx * nx + ny * ny
                if r2 > 1.0:
                    # outside sphere -> transparent (or background)
                    continue
                z = math.sqrt(1.0 - r2)
                # now compute lon, lat
                lon = math.atan2(nx, z) + phase  # -pi..pi offset by phase
                lat = math.asin(ny)  # -pi/2 .. pi/2
                # map lon/lat to source equirectangular coordinates
                src_x = (lon / (2 * math.pi) + 0.5) * src_w
                src_y = (0.5 - lat / math.pi) * src_h
                sx = int(src_x) % src_w
                sy = int(max(0, min(src_h - 1, src_y)))
                dst[y, x] = src_np[sy, sx]
        return Image.fromarray(dst, "RGBA")

    @app_commands.command(name="globe", description="Wrap an image onto a rotating globe (exports a GIF).")
    @cooldown(cl=20, tm=30.0, ft=3)
    async def globe(
        self,
        interaction: discord.Interaction,
        rotations: int = 1,
        frames_count: int = 24,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        frames_count = max(8, min(64, frames_count))
        rotations = max(1, min(10, rotations))

        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"Globe invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"Globe invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")

        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Globe no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        src_frames, _ = self._load_frames_from_bytes(data)
        base = src_frames[0].convert("RGBA")
        # choose a reasonable output size
        out_w = min(600, base.width)
        out_h = out_w  # square for sphere
        base_small = base.resize((out_w * 2, out_h), Image.LANCZOS)  # expect equirectangular (w ~ 2*h) but we scale
        # create frames
        globe_frames = []
        for i in range(frames_count):
            phase = 2 * math.pi * (i / frames_count) * rotations
            frm = self._sphere_project_frame(base_small, phase, (out_w, out_h))
            globe_frames.append(frm)

        gif = self._frames_to_gif_bytes(globe_frames, duration_ms=80)
        log.successtrace(f"Globe success for {interaction.user.id}")
        await self._send_image_bytes(interaction, gif, "globe.gif")

    @app_commands.command(name="blur", description="Apply a blur effect to an image.")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def blur(
        self,
        interaction: discord.Interaction,
        radius: float = 5.0,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        radius = max(0.1, min(50.0, radius))

        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Blur no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=1200)

        out_frames = []
        for f in frames:
            blurred = f.filter(ImageFilter.GaussianBlur(radius=radius))
            out_frames.append(blurred)

        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        log.successtrace(f"Blur success for {interaction.user.id} (radius: {radius})")
        await self._send_image_bytes(interaction, gif, "blurred.gif")

    @app_commands.command(name="hueshift", description="Shift the hue of an image (wraps around HSV color wheel).")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def hueshift(
        self,
        interaction: discord.Interaction,
        shift: float = 0.1,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        shift = shift % 1.0  # normalize [0, 1)

        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"Hueshift invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"Hueshift invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")

        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Hueshift no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=1200)

        # hue shift amount in integer range (0–255)
        # PIL's HSV mode: H is 0-255 (represents 0-360 degrees)
        shift_amount = int(round(shift * 255))

        out_frames = []
        for f in frames:
            # Convert to RGB first to ensure proper color space
            rgb = f.convert("RGB")
            hsv = rgb.convert("HSV")
            np_hsv = np.array(hsv, dtype=np.uint8)

            # Only modify hue channel (index 0), ensure it wraps correctly
            hue_channel = np_hsv[..., 0].astype(np.uint16)  # Use uint16 to avoid overflow
            hue_channel = (hue_channel + shift_amount) % 256
            np_hsv[..., 0] = hue_channel.astype(np.uint8)

            # Convert back: HSV -> RGB -> RGBA
            shifted_rgb = Image.fromarray(np_hsv, "HSV").convert("RGB")
            # Preserve alpha if original had it
            if f.mode == 'RGBA':
                shifted = shifted_rgb.convert("RGBA")
                # Copy alpha channel from original
                alpha = f.split()[3]
                shifted.putalpha(alpha)
            else:
                shifted = shifted_rgb.convert("RGBA")
            out_frames.append(shifted)

        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        log.successtrace(f"Hueshift success for {interaction.user.id} (shift: {shift})")
        await self._send_image_bytes(interaction, gif, "hueshifted.gif")
    
    @app_commands.command(name="invert", description="Invert the colors of an image.")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def invert(
        self,
        interaction: discord.Interaction,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()

        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Invert no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=1200)

        out_frames = []
        for f in frames:
            r, g, b, a = f.split()
            r = r.point(lambda i: 255 - i)
            g = g.point(lambda i: 255 - i)
            b = b.point(lambda i: 255 - i)
            inverted = Image.merge("RGBA", (r, g, b, a))
            out_frames.append(inverted)

        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        log.successtrace(f"Invert success for {interaction.user.id}")
        await self._send_image_bytes(interaction, gif, "inverted.gif")
    
    @app_commands.command(name="speechbubble", description="Add a speech bubble caption to an image (caption is optional).")
    @app_commands.describe(
        position="Where the bubble tail points (left, right)",
        caption="Text to put in the bubble (optional)"
    )
    @app_commands.choices(position=[
        app_commands.Choice(name="Left", value="left"),
        app_commands.Choice(name="Right", value="right"),
    ])
    @cooldown(cl=15, tm=30.0, ft=3)
    async def speechbubble(
        self,
        interaction: discord.Interaction,
        position: app_commands.Choice[str],
        caption: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()

        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"Speechbubble invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"Speechbubble invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")

        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Speechbubble no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=900)

        bubble_path = os.path.join(os.getcwd(), "resources", "bubbles", f"{position.value}.png")

        if not os.path.exists(bubble_path):
            log.error(f"Speechbubble template missing: {bubble_path}")
            return await interaction.followup.send(f"❌ Missing bubble template for '{position.value}'!", ephemeral=True)

        bubble_base = Image.open(bubble_path).convert("RGBA")

        out_frames = []
        for frame in frames:
            tmp = frame.copy().convert("RGBA")
            w, h = tmp.size

            # Calculate bubble size based on whether there's text
            if caption:
                # Target height based on text needs (20-25% of image)
                target_h = int(h * 0.18)
            else:
                # Smaller bubble if no text
                target_h = int(h * 0.12)
            target_w = int(w * 0.85)  # 85% width for better proportions

            # Resize bubble maintaining aspect ratio better
            bubble_w, bubble_h = bubble_base.size
            aspect_ratio = bubble_w / bubble_h
            calculated_w = int(target_h * aspect_ratio)
            if calculated_w > target_w:
                # If calculated width is too wide, scale down
                target_w = calculated_w
                if target_w > w * 0.9:
                    target_w = int(w * 0.9)
                    target_h = int(target_w / aspect_ratio)

            bubble = bubble_base.resize((target_w, target_h), Image.LANCZOS)

            # Position bubble near top
            bx = int((w - target_w) / 2)
            by = int(h * 0.05)

            tmp.alpha_composite(bubble, (bx, by))

            # Draw text inside bubble if caption provided
            if caption:
                draw = ImageDraw.Draw(tmp)
                font = ImageFont.truetype(os.path.join(os.getcwd(), "resources", "impact.ttf"), 36)
                padding = int(target_h * 0.15)
                text_box_w = target_w - padding * 2

                lines = self.wrap_text(caption, font, text_box_w)
                line_height = font.getbbox("Ay")[3]
                total_text_height = len(lines) * line_height
                centered_y = by + (target_h - total_text_height) // 2

                for i, line in enumerate(lines):
                    lw = font.getlength(line)
                    tx = bx + (target_w - lw) / 2
                    ty = centered_y + i * line_height
                    draw.text(
                        (tx, ty),
                        line,
                        font=font,
                        fill=(0, 0, 0),
                        stroke_width=2,
                        stroke_fill=(255, 255, 255)
                    )

            out_frames.append(tmp)

        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        log.successtrace(f"Speechbubble success for {interaction.user.id}")
        await self._send_image_bytes(interaction, gif, "speechbubble.gif")

    @app_commands.command(name="swirl", description="Apply a swirl effect to an image.")
    @cooldown(cl=15, tm=30.0, ft=3)
    async def swirl(
        self,
        interaction: discord.Interaction,
        strength: float = 2.0,
        radius: float = 100.0,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        strength = max(0.1, min(10.0, strength))
        radius = max(10.0, min(500.0, radius))

        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"Swirl invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"Swirl invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")
        
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            log.warningtrace(f"Swirl no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=900)

        out_frames = []
        for f in frames:
            np_img = np.array(f.convert("RGBA"))
            h, w = np_img.shape[:2]
            cx, cy = w / 2, h / 2
            dst = np.zeros_like(np_img)

            for y in range(h):
                for x in range(w):
                    dx = x - cx
                    dy = y - cy
                    dist = math.sqrt(dx * dx + dy * dy)
                    if dist < radius:
                        angle = strength * (radius - dist) / radius
                        s = math.sin(angle)
                        c = math.cos(angle)
                        src_x = int(cx + c * dx - s * dy)
                        src_y = int(cy + s * dx + c * dy)
                    else:
                        src_x, src_y = x, y
                    src_x = max(0, min(w - 1, src_x))
                    src_y = max(0, min(h - 1, src_y))
                    dst[y, x] = np_img[src_y, src_x]

            out_frame = Image.fromarray(dst, "RGBA")
            out_frames.append(out_frame)

        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        log.successtrace(f"Swirl success for {interaction.user.id}")
        await self._send_image_bytes(interaction, gif, "swirled.gif")

    @app_commands.command(name="imagefy", description="Convert last image sent by bot to PNG or JPG.")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def imagefy(
        self,
        interaction: discord.Interaction,
        format: Literal["png", "jpg"] = "png",
    ):
        await interaction.response.defer(thinking=True)

        channel = interaction.channel
        if not channel:
            log.error("Imagefy channel access failed")
            return await interaction.followup.send("❌ Could not access channel.", ephemeral=True)

        # Find last bot message with attachment
        last_msg = None
        async for msg in channel.history(limit=50):
            if msg.author.id == self.bot.user.id and msg.attachments:
                last_msg = msg
                break

        if not last_msg:
            log.warningtrace(f"Imagefy no message found in {channel.id}")
            return await interaction.followup.send(
                "❌ No recent bot message with an attachment found.",
                ephemeral=True,
            )

        attachment = last_msg.attachments[0]
        data = await self._fetch_bytes(attachment, None)
        if not data:
            log.error("Imagefy attachment fetch failed")
            return await interaction.followup.send(
                "❌ Failed to fetch the attachment.", ephemeral=True
            )

        # Load frames
        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=1200)

        out_frames = []
        for f in frames:
            if format == "png":
                out_frames.append(f.convert("RGBA"))
            else:
                out_frames.append(f.convert("RGB"))

        # --- PNG Output ---
        if format == "png":
            bio = io.BytesIO()
            if len(out_frames) == 1:
                out_frames[0].save(bio, format="PNG")
            else:
                out_frames[0].save(
                    bio,
                    format="PNG",
                    save_all=True,
                    append_images=out_frames[1:],
                    loop=0,
                    duration=duration,
                )
            bio.seek(0)
            log.successtrace(f"Imagefy success (png) for {interaction.user.id}")
            await interaction.followup.send(file=discord.File(bio, "converted.png"))
            return

        # --- JPG Output ---
        bio = io.BytesIO()
        if len(out_frames) == 1:
            out_frames[0].save(bio, format="JPEG", quality=90)
            bio.seek(0)
            log.successtrace(f"Imagefy success (jpg) for {interaction.user.id}")
            await interaction.followup.send(file=discord.File(bio, "converted.jpg"))
        else:
            # Multi-frame JPG: zip frames individually
            zip_bio = io.BytesIO()
            with zipfile.ZipFile(zip_bio, "w", zipfile.ZIP_DEFLATED) as zipf:
                for i, frame in enumerate(out_frames):
                    frame_bio = io.BytesIO()
                    frame.convert("RGB").save(frame_bio, format="JPEG", quality=90)
                    frame_bio.seek(0)
                    zipf.writestr(f"frame_{i+1}.jpg", frame_bio.read())
            zip_bio.seek(0)
            log.successtrace(f"Imagefy success (zip) for {interaction.user.id}")
            await interaction.followup.send(
                "🗜️ Multiple frames detected! Exported as ZIP of JPGs:",
                file=discord.File(zip_bio, "frames.zip"),
            )
    
    @app_commands.command(name="qrcode", description="Generate or read a QR code.")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def qrcode(
        self,
        interaction: discord.Interaction,
        data: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        if data:
            # Generate QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4
            )
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            bio = io.BytesIO()
            img.save(bio, format="PNG")
            bio.seek(0)
            log.successtrace(f"QR code generated for {interaction.user.id}")
            await interaction.followup.send(file=discord.File(bio, "qrcode.png"))
            return

        if image and (image.filename.lower().endswith(EXT_BLACKLIST)):
            log.warningtrace(f"QR invalid image extension by {interaction.user.id}: {image.filename}")
            return await interaction.followup.send("❌ Invalid image extension! Try using a PNG, WEBP or JPEG.")
        elif image_url and image_url.split("?")[0].lower().endswith(EXT_BLACKLIST):
            log.warningtrace(f"QR invalid url extension by {interaction.user.id}: {image_url}")
            return await interaction.followup.send("❌ Invalid url extension! Try using a PNG, WEBP or JPEG.")

        # Else, read QR code from image
        if not ZBAR_AVAILABLE:
            log.warningtrace(f"QR read attempted by {interaction.user.id} but ZBar is missing")
            return await interaction.followup.send("❌ QR code scanning is unavailable on this host (missing zbar library). Generation is still available.", ephemeral=True)

        data_bytes = await self._resolve_image_bytes(interaction, image, image_url)
        if not data_bytes:
            log.warningtrace(f"QR no data found for {interaction.user.id}")
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, _ = self._load_frames_from_bytes(data_bytes)

        decoded_objs = []
        for frame in frames:
            gray = ImageOps.grayscale(frame)
            decoded_objs = decode(gray)
            if decoded_objs:
                break

        if not decoded_objs:
            log.warningtrace(f"No QR code detected for {interaction.user.id}")
            return await interaction.followup.send("❌ No QR code detected in the image.", ephemeral=True)

        messages = []
        long_texts = []
        for i, obj in enumerate(decoded_objs, 1):
            qr_type = obj.type or "Unknown"
            coords = obj.rect  # namedtuple: left, top, width, height
            data_str = obj.data.decode("utf-8", errors="ignore").strip() or "(empty)"

            if len(data_str) > 800:
                # too long — export to text
                filename = f"qrcode_{i}.txt"
                long_texts.append((filename, data_str))
                preview = f"[Content too long → exported as `{filename}`]"
            else:
                preview = f"`{data_str}`"

            messages.append(
                f"**QR #{i}**\n"
                f"📦 Type: `{qr_type}`\n"
                f"🗺️ Position: (x={coords.left}, y={coords.top}, w={coords.width}, h={coords.height})\n"
                f"💬 Data: {preview}"
            )

        # Send message and any text attachments
        files = []
        for filename, text in long_texts:
            bio = io.BytesIO(text.encode("utf-8"))
            bio.seek(0)
            files.append(discord.File(bio, filename=filename))

        embed = discord.Embed(
            title=f"🧾 QR Scan Results ({len(decoded_objs)} found)",
            color=0x2ECC71,
        )
        embed.description = "\n\n".join(messages)[:4000]  # safeguard against embed limits
        log.successtrace(f"QR code read for {interaction.user.id} ({len(decoded_objs)} found)")
        await interaction.followup.send(embed=embed, files=files, ephemeral=False)

class ImageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        while True:
            now = time.time()
            expired = [uid for uid, (_, exp) in USER_SELECTED.items() if exp < now]
            for uid in expired:
                del USER_SELECTED[uid]
            await asyncio.sleep(300)

    async def cog_load(self):
        self.bot.tree.add_command(ImageCommands(self.bot))
        self.bot.tree.add_command(select_image)

async def setup(bot):
    await bot.add_cog(ImageCog(bot))

# hopefully it dont consume my entire ram :D