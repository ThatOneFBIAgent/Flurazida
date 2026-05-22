# tests/test_image.py
# Pytest suite for checking image manipulation helper functions and slash commands in commands/image.py

import sys
import os
import io
import math
import pytest
from unittest.mock import AsyncMock, MagicMock
from PIL import Image, ImageFont, ImageSequence

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.image import ImageCommands
from discord.app_commands import Choice
import config


# ===================== Pytest Fixtures =====================

@pytest.fixture(autouse=True)
def reset_cooldowns():
    """Autouse fixture to reset the command cooldown state before every test."""
    config._user_command_cooldowns.clear()


# ===================== Helper Functions to Generate Dummy Image Bytes =====================

def create_dummy_png_bytes(width=100, height=100, color=(255, 0, 0, 255)):
    """Generate a valid in-memory static PNG image as bytes."""
    im = Image.new("RGBA", (width, height), color)
    bio = io.BytesIO()
    im.save(bio, format="PNG")
    return bio.getvalue()


def create_dummy_gif_bytes(width=100, height=100, frames_colors=None):
    """Generate a valid in-memory animated GIF image as bytes."""
    if frames_colors is None:
        frames_colors = [(255, 0, 0, 255), (0, 255, 0, 255)]
        
    frames = []
    for color in frames_colors:
        frames.append(Image.new("RGBA", (width, height), color))
        
    bio = io.BytesIO()
    frames[0].save(
        bio,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0
    )
    return bio.getvalue()


def get_test_font():
    """Load impact.ttf if available, else load default PIL font."""
    font_path = os.path.join(os.getcwd(), "resources", "impact.ttf")
    if os.path.exists(font_path):
        return ImageFont.truetype(font_path, 20)
    return ImageFont.load_default()


# ===================== Unit Tests for Core Image Manipulation Helpers =====================

class TestImageHelpers:
    def test_load_frames_from_bytes_static(self):
        """Should correctly load a static PNG image and return a single frame."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        png_data = create_dummy_png_bytes(120, 80)
        
        frames, duration = cmd._load_frames_from_bytes(png_data)
        assert len(frames) == 1
        assert frames[0].size == (120, 80)
        assert duration == 80  # Default duration fallback

    def test_load_frames_from_bytes_animated(self):
        """Should correctly load animated GIF frames and accumulate frames."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        gif_data = create_dummy_gif_bytes(100, 100, [(255, 0, 0, 255), (0, 0, 255, 255)])
        
        frames, duration = cmd._load_frames_from_bytes(gif_data)
        assert len(frames) == 2
        assert frames[0].size == (100, 100)
        assert duration == 100

    def test_frames_to_gif_bytes_single(self):
        """Should convert a single frame back to PNG bytes."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        frame = Image.new("RGBA", (50, 50), (255, 0, 0, 255))
        
        res = cmd._frames_to_gif_bytes([frame])
        im = Image.open(io.BytesIO(res))
        assert im.format == "PNG"
        assert im.size == (50, 50)

    def test_frames_to_gif_bytes_multiple(self):
        """Should convert multiple frames back to GIF bytes."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        frame1 = Image.new("RGBA", (50, 50), (255, 0, 0, 255))
        frame2 = Image.new("RGBA", (50, 50), (0, 255, 0, 255))
        
        res = cmd._frames_to_gif_bytes([frame1, frame2], duration_ms=120)
        im = Image.open(io.BytesIO(res))
        assert im.format == "GIF"
        
        frames = list(ImageSequence.Iterator(im))
        assert len(frames) == 2

    def test_wrap_text(self):
        """Should wrap text lines properly under specified maximum width constraint."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        font = get_test_font()
        
        text = "This is a very long sentence that will definitely wrap multiple times"
        wrapped = cmd.wrap_text(text, font, max_width=60)
        assert len(wrapped) > 1

    def test_draw_text_centered(self):
        """Should draw text block on a larger canvas containing centered text caption."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        img = Image.new("RGBA", (150, 150), (255, 0, 0, 255))
        font = get_test_font()
        wrapped_lines = ["Centered Line 1", "Centered Line 2"]
        
        # Test Top box placement (bottom=False)
        out_top = cmd._draw_text_centered(img, wrapped_lines, font, box_h=50, bottom=False)
        assert out_top.size == (150, 200)
        
        # Test Bottom box placement (bottom=True)
        out_bottom = cmd._draw_text_centered(img, wrapped_lines, font, box_h=50, bottom=True)
        assert out_bottom.size == (150, 200)

    def test_flip_frame(self):
        """Should correctly flip a frame horizontally, vertically, or both."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        img = Image.new("RGBA", (100, 100))
        
        assert cmd._flip_frame(img, "horizontal").size == (100, 100)
        assert cmd._flip_frame(img, "vertical").size == (100, 100)
        assert cmd._flip_frame(img, "both").size == (100, 100)

    def test_jpegify_bytes(self):
        """Should apply jpeg artifact compression with optional downscaling."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        img = Image.new("RGBA", (120, 120), (255, 0, 0, 255))
        
        # Test with scale_down=True
        out_scaled = cmd._jpegify_bytes([img], recursions=2, quality=5, scale_down=True)
        assert len(out_scaled) == 1
        assert out_scaled[0].size == (120, 120)
        
        # Test with scale_down=False
        out_normal = cmd._jpegify_bytes([img], recursions=1, quality=10, scale_down=False)
        assert len(out_normal) == 1
        assert out_normal[0].size == (120, 120)

    def test_motion_blur_frame(self):
        """Should apply motion blur by shifting and blending the image."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        img = Image.new("RGBA", (60, 60), (255, 0, 0, 255))
        
        # Radius <= 1 should be a no-op returning original image
        assert cmd._motion_blur_frame(img, radius=1.0, angle=45.0) == img
        
        # Radius > 1 should apply blur/blending
        blurred = cmd._motion_blur_frame(img, radius=8.0, angle=30.0)
        assert blurred.size == (60, 60)

    def test_resize_if_needed(self):
        """Should resize frame only if largest dimension exceeds the max limit."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        
        # Small frame: no change
        small = Image.new("RGBA", (80, 80))
        assert cmd._resize_if_needed([small], max_dim=100)[0].size == (80, 80)
        
        # Large frame: scaled down keeping aspect ratio
        large = Image.new("RGBA", (400, 200))
        assert cmd._resize_if_needed([large], max_dim=200)[0].size == (200, 100)

    def test_sphere_project_frame(self):
        """Should project frame coordinates onto a sphere shape."""
        bot = MagicMock()
        cmd = ImageCommands(bot)
        img = Image.new("RGBA", (100, 50), (255, 0, 0, 255))
        
        projected = cmd._sphere_project_frame(img, phase=1.0, out_size=(50, 50))
        assert projected.size == (50, 50)


# ===================== Parametrized Test for Hueshift Math =====================

@pytest.mark.parametrize(
    "shift_in,expected_shift_out",
    [
        (0.1, 0.1),
        (0.5, 0.5),
        (0.0, 0.0),
        (1.0, 0.0), # 1.0 represents a full 360 rotation, wrapping back to 0.0
        (180.0, 0.5),
        (360.0, 0.0),
        (90.0, 0.25),
        (720.0, 0.0),
        (-90.0, 0.75),
        (-180.0, 0.5),
        (-0.2, 0.8),
    ]
)
def test_hueshift_normalization(shift_in, expected_shift_out):
    """Verify both degree (0-360) and fraction (0-1.0) values are correctly normalized to [0, 1)."""
    shift = shift_in
    if abs(shift) > 1.0:
        shift = shift / 360.0
    shift = shift % 1.0
    assert pytest.approx(shift) == expected_shift_out


# ===================== Async Tests for Overhauled Command Actions =====================

@pytest.mark.asyncio
async def test_caption_command_static():
    """Verify that caption command processes static images correctly."""
    bot = MagicMock()
    cmd = ImageCommands(bot)
    
    interaction = AsyncMock()
    interaction.user.id = 12345
    
    attachment = AsyncMock()
    attachment.filename = "test.png"
    attachment.read.return_value = create_dummy_png_bytes()
    
    cmd._send_image_bytes = AsyncMock()
    
    # We call `.callback` directly as the command is decorated with app_commands.command
    await cmd.caption_image.callback(cmd, interaction, caption="Premium Caption text", bottom=False, image=attachment)
    
    interaction.response.defer.assert_called_once()
    cmd._send_image_bytes.assert_called_once()
    args, kwargs = cmd._send_image_bytes.call_args
    assert args[2] == "captioned.png"


@pytest.mark.asyncio
async def test_caption_command_gif():
    """Verify that caption command processes animated GIFs correctly."""
    bot = MagicMock()
    cmd = ImageCommands(bot)
    
    interaction = AsyncMock()
    interaction.user.id = 12345
    
    attachment = AsyncMock()
    attachment.filename = "test.gif"
    attachment.read.return_value = create_dummy_gif_bytes()
    
    cmd._send_image_bytes = AsyncMock()
    
    await cmd.caption_image.callback(cmd, interaction, caption="Premium GIF Caption", bottom=True, image=attachment)
    
    interaction.response.defer.assert_called_once()
    cmd._send_image_bytes.assert_called_once()
    args, kwargs = cmd._send_image_bytes.call_args
    assert args[2] == "captioned.gif"


@pytest.mark.asyncio
async def test_jpegify_command():
    """Verify that jpegify command executes successfully with custom arguments."""
    bot = MagicMock()
    cmd = ImageCommands(bot)
    
    interaction = AsyncMock()
    interaction.user.id = 12345
    
    attachment = AsyncMock()
    attachment.filename = "test.png"
    attachment.read.return_value = create_dummy_png_bytes()
    
    cmd._send_image_bytes = AsyncMock()
    
    await cmd.jpegify.callback(cmd, interaction, recursions=2, quality=12, scale_down=True, image=attachment)
    
    interaction.response.defer.assert_called_once()
    cmd._send_image_bytes.assert_called_once()
    args, kwargs = cmd._send_image_bytes.call_args
    assert "jpegified_q12_x2.gif" in args[2]


@pytest.mark.asyncio
async def test_blur_command():
    """Verify that blur command handles all three blur options (Gaussian, Box, Motion)."""
    bot = MagicMock()
    cmd = ImageCommands(bot)
    
    interaction = AsyncMock()
    interaction.user.id = 12345
    
    attachment = AsyncMock()
    attachment.filename = "test.png"
    attachment.read.return_value = create_dummy_png_bytes()
    
    cmd._send_image_bytes = AsyncMock()
    
    # 1) Gaussian Blur
    await cmd.blur.callback(cmd, interaction, type="gaussian", radius=6.0, image=attachment)
    assert cmd._send_image_bytes.call_count == 1
    assert cmd._send_image_bytes.call_args[0][2] == "blurred_gaussian.gif"
    
    # 2) Box Blur
    config._user_command_cooldowns.clear()
    cmd._send_image_bytes.reset_mock()
    await cmd.blur.callback(cmd, interaction, type="box", radius=4.0, image=attachment)
    assert cmd._send_image_bytes.call_count == 1
    assert cmd._send_image_bytes.call_args[0][2] == "blurred_box.gif"
    
    # 3) Motion Blur
    config._user_command_cooldowns.clear()
    cmd._send_image_bytes.reset_mock()
    await cmd.blur.callback(cmd, interaction, type="motion", radius=10.0, angle=45.0, image=attachment)
    assert cmd._send_image_bytes.call_count == 1
    assert cmd._send_image_bytes.call_args[0][2] == "blurred_motion.gif"


@pytest.mark.asyncio
async def test_hueshift_command():
    """Verify that hueshift command processes image shift successfully."""
    bot = MagicMock()
    cmd = ImageCommands(bot)
    
    interaction = AsyncMock()
    interaction.user.id = 12345
    
    attachment = AsyncMock()
    attachment.filename = "test.png"
    attachment.read.return_value = create_dummy_png_bytes()
    
    cmd._send_image_bytes = AsyncMock()
    
    await cmd.hueshift.callback(cmd, interaction, shift=180.0, image=attachment)
    
    interaction.response.defer.assert_called_once()
    cmd._send_image_bytes.assert_called_once()
    args, kwargs = cmd._send_image_bytes.call_args
    assert args[2] == "hueshifted.gif"


@pytest.mark.asyncio
async def test_speechbubble_command():
    """Verify that speechbubble command processes overlay and cutout styles correctly."""
    bot = MagicMock()
    cmd = ImageCommands(bot)
    
    interaction = AsyncMock()
    interaction.user.id = 12345
    
    attachment = AsyncMock()
    attachment.filename = "test.png"
    attachment.read.return_value = create_dummy_png_bytes()
    
    cmd._send_image_bytes = AsyncMock()
    
    position_choice = Choice(name="Left", value="left")
    
    # Opaque overlay
    await cmd.speechbubble.callback(
        cmd,
        interaction,
        position=position_choice,
        caption="Overlay caption text",
        style="overlay",
        image=attachment
    )
    assert cmd._send_image_bytes.call_count == 1
    assert cmd._send_image_bytes.call_args[0][2] == "speechbubble.gif"
    
    # Transparent cutout
    config._user_command_cooldowns.clear()
    cmd._send_image_bytes.reset_mock()
    await cmd.speechbubble.callback(
        cmd,
        interaction,
        position=position_choice,
        caption="Cutout caption text",
        style="cutout",
        image=attachment
    )
    assert cmd._send_image_bytes.call_count == 1
    assert cmd._send_image_bytes.call_args[0][2] == "speechbubble.gif"
