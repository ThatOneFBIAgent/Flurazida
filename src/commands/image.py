from re import A
import io, time, math, asyncio, logging, aiohttp, imageio, discord, os
from typing import Dict, Optional, Tuple, List, Literal
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageSequence
from discord import app_commands
from discord.ui import View, Button
from discord.ext import commands
from config import cooldown, safe_command

log = logging.getLogger(__name__)

# Global image selection memory
USER_SELECTED: Dict[int, Tuple[str, float]] = {}

# View for selecting an image from multiple attachments
class ImageSelectView(View):
    def __init__(self, interaction: discord.Interaction, attachments: list[discord.Attachment]):
        super().__init__(timeout=30)
        self.interaction = interaction
        self.attachments = attachments
        self.selected_url = None
        self.selected_index = None

        for i, att in enumerate(attachments[:5]):  # only show up to 5 buttons to avoid clutter
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


# select image stuff
@app_commands.context_menu(name="Select image")
async def select_image(interaction: discord.Interaction, message: discord.Message):
    """Context menu to select an image from a message and store it for 30 minutes."""
    await interaction.response.defer(ephemeral=True)

    valid_attachments = [
        a for a in message.attachments
        if a.filename and a.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))
    ]

    if not valid_attachments:
        await interaction.followup.send("❌ No valid images found in that message.", ephemeral=True)
        return None, None

    # single attachment -> auto select and store
    if len(valid_attachments) == 1:
        att = valid_attachments[0]
        USER_SELECTED[interaction.user.id] = (att.url, time.time() + 30 * 60)
        await interaction.followup.send(
            f"✅ Image #1 selected automatically ({att.filename})",
            ephemeral=True
        )
        return att.url, att.filename

    # multiple -> show buttons to allow the user to pick which one
    view = ImageSelectView(interaction, valid_attachments)
    await interaction.followup.send("🖼️ Multiple images found! Pick one:", view=view, ephemeral=True)
    await view.wait()

    # if user canceled or timed out
    if not view.selected_url:
        await interaction.followup.send("❌ No image selected (timed out or cancelled).", ephemeral=True)
        return None, None

    # selection was stored inside the button callback; return for convenience
    return view.selected_url, valid_attachments[view.selected_index - 1].filename

class ImageCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Start cleanup loop
        self.bot.loop.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        """Periodically clean up expired selections."""
        while True:
            now = time.time()
            expired = [uid for uid, (_, exp) in USER_SELECTED.items() if exp < now]
            for uid in expired:
                del USER_SELECTED[uid]
            await asyncio.sleep(300)

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
        try:
            async with aiohttp.ClientSession() as session:
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
            # save single-frame GIF
            frames[0].save(bio, format="GIF")
        else:
            first, rest = frames[0], frames[1:]
            # PIL handles conversion
            first.save(
                bio,
                format="GIF",
                save_all=True,
                append_images=rest,
                loop=loop,
                duration=duration_ms,
                disposal=2,
            )
        bio.seek(0)
        return bio.read()

    async def _send_image_bytes(self, interaction: discord.Interaction, data: bytes, filename: str, ephemeral: bool = False):
        """Helper to reply with a file."""
        await interaction.followup.send(file=discord.File(io.BytesIO(data), filename=filename), ephemeral=ephemeral)
   
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
        bg_color: Tuple[int,int,int,int] = (255,255,255,255)
    ) -> Image.Image:
        """Return new image with a white box on top or bottom and centered, wrapped text inside."""
        w, h = img.size
        font_size = max_font_size
        wrapped_lines = []

        # Shrink font until text fits vertically (max half of image height)
        while font_size > 6:
            font = ImageFont.truetype(font_path, font_size)
            lines = []
            for paragraph in text.split("\n"):
                lines.extend(self.wrap_text(paragraph, font, w - 2*padding))
            line_height = font.getbbox("Ay")[3]
            box_height = len(lines) * line_height + 2*padding
            if box_height <= h // 2:
                wrapped_lines = lines
                break
            font_size -= 2

        if not wrapped_lines:
            # fallback if still too tall
            font = ImageFont.truetype(font_path, font_size)
            wrapped_lines = self.wrap_text(text, font, w - 2*padding)
            line_height = font.getbbox("Ay")[3]
            box_height = len(wrapped_lines) * line_height + 2*padding

        # Create new image with extra box height
        new_h = h + box_height
        new_img = Image.new("RGBA", (w, new_h), (0,0,0,0))

        if bottom:
            new_img.paste(img, (0,0))
            box_y = h
        else:
            new_img.paste(img, (0, box_height))
            box_y = 0

        draw = ImageDraw.Draw(new_img)
        draw.rectangle([0, box_y, w, box_y + box_height], fill=bg_color)

        # Draw each line centered
        for i, line in enumerate(wrapped_lines):
            bbox = draw.textbbox((0,0), line, font=font)
            text_w, text_h = bbox[2]-bbox[0], bbox[3]-bbox[1]
            text_x = (w - text_w)//2
            text_y = box_y + padding + i*line_height
            draw.text((text_x, text_y), line, font=font, fill=(0,0,0,255))

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

    # @safe_command(timeout=15.0)
    @app_commands.command(name="forcegif", description="Convert an image (or gif) to a forced GIF output.")
    @cooldown(10)
    async def force_gif(
        self,
        interaction: discord.Interaction,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        # make at least 2 identical frames for better autoplay behavior
        if len(frames) == 1:
            frames = frames * 2
        # ensure not huge
        frames = self._resize_if_needed(frames, max_dim=900)
        gif = self._frames_to_gif_bytes(frames, duration_ms=duration)
        await self._send_image_bytes(interaction, gif, "forced.gif")

    # @safe_command(timeout=15.0)
    @app_commands.command(name="caption", description="Add a caption at the top of an image (accepts gifs).")
    @cooldown(15)
    async def caption_image(
        self,
        interaction: discord.Interaction,
        caption: str,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=900)

        font = ImageFont.truetype(os.path.join(os.getcwd(), "resources", "impact.ttf"), 48)
        out_frames = []
        for f in frames:
            tmp = f.copy().convert("RGBA")
            tmp = self._draw_text_centered(
                tmp,
                caption,
                bottom=False,
                font_path=os.path.join(os.getcwd(), "resources", "impact.ttf"),
                max_font_size=64
            )
            out_frames.append(tmp)

        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        await self._send_image_bytes(interaction, gif, "caption_top.gif")

    # @safe_command(timeout=15.0)
    @app_commands.command(name="caption2", description="Add a caption at the bottom of an image (accepts gifs).")
    @cooldown(15)
    async def caption2_image(
        self,
        interaction: discord.Interaction,
        caption: str,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=900)

        font = ImageFont.truetype(os.path.join(os.getcwd(), "resources", "impact.ttf"), 48)
        out_frames = []
        for f in frames:
            tmp = f.copy().convert("RGBA")
            tmp = self._draw_text_centered(
                tmp,
                caption,
                bottom=True,
                font_path=os.path.join(os.getcwd(), "resources", "impact.ttf"),
                max_font_size=64
            )
            out_frames.append(tmp)

        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        await self._send_image_bytes(interaction, gif, "caption_bottom.gif")

    # @safe_command(timeout=15.0)
    @app_commands.command(name="jpegify", description="Apply JPEG artifacting. Set recursions to repeat the effect.")
    @cooldown(10)
    async def jpegify(
        self,
        interaction: discord.Interaction,
        recursions: int = 1,
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        recursions = max(1, min(25, recursions))
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)

        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=900)
        out_frames = self._jpegify_bytes(frames, recursions=recursions, quality=18)
        gif = self._frames_to_gif_bytes(out_frames, duration_ms=duration)
        await self._send_image_bytes(interaction, gif, f"jpegified_x{recursions}.gif")

    # @safe_command(timeout=15.0)
    @app_commands.command(name="avatar", description="Get a user's avatar (or your own by default).")
    @cooldown(5)
    async def avatar(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ):
        await interaction.response.defer()
        target = user or interaction.user
        url = target.display_avatar.replace(size=1024).url
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return await interaction.followup.send("❌ Failed to fetch avatar.")
                data = await r.read()
        await self._send_image_bytes(interaction, data, f"{target.id}_avatar.png")

    # @safe_command(timeout=15.0)
    @app_commands.command(name="serveravatar", description="Get the server (guild) icon.")
    @cooldown(5)
    async def serveravatar(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.guild:
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)
        icon = interaction.guild.icon
        if not icon:
            return await interaction.followup.send("❌ This server has no icon.", ephemeral=True)
        url = interaction.guild.icon.replace(size=1024).url
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return await interaction.followup.send("❌ Failed to fetch server icon.")
                data = await r.read()
        await self._send_image_bytes(interaction, data, f"{interaction.guild.id}_icon.png")

    # @safe_command(timeout=15.0)
    @app_commands.command(name="flip", description="Flip an image horizontally/vertically or both.")
    @cooldown(10)
    async def flip(
        self,
        interaction: discord.Interaction,
        axis: Literal["horizontal", "vertical", "both"] = "horizontal",
        image: Optional[discord.Attachment] = None,
        image_url: Optional[str] = None,
    ):
        await interaction.response.defer()
        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
            return await interaction.followup.send("❌ No image provided or selection found.", ephemeral=True)
        frames, duration = self._load_frames_from_bytes(data)
        frames = self._resize_if_needed(frames, max_dim=1200)
        out = [self._flip_frame(f, axis) for f in frames]
        gif = self._frames_to_gif_bytes(out, duration_ms=duration)
        await self._send_image_bytes(interaction, gif, f"flipped_{axis}.gif")

    # --------------------
    # Globe effect
    # --------------------
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

    # @safe_command(timeout=30.0)
    @app_commands.command(name="globe", description="Wrap an image onto a rotating globe (exports a GIF).")
    @cooldown(20)
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

        data = await self._resolve_image_bytes(interaction, image, image_url)
        if not data:
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
        await self._send_image_bytes(interaction, gif, "globe.gif")


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageCommands(bot))
    try:
        bot.tree.add_command(select_image)
    except Exception as e:
        log.exception("Failed to add context menu command: %s", e)
        
# holy fuck lois
