import discord, random, asyncio
from discord.ext import commands
from discord import app_commands, Interaction, ui
from database import update_balance, get_balance
from config import cooldown

from logger import get_logger

log = get_logger()

def resolve_bet_input(bet_input, user_id):
    """
    Accepts a user-provided bet (string or int).
    Supports "*", "all", "max", "allin" (case-insensitive) for all-in.
    Returns an int bet on success, or None on invalid.
    """
    bal = get_balance(user_id)
    if isinstance(bet_input, int):
        b = bet_input
    else:
        s = str(bet_input).strip().lower()
        if s in ("*", "all", "max", "allin", "all-in"):
            return bal
        try:
            b = int(s)
        except Exception:
            return None
    if b <= 0:
        return None
    if b > bal:
        return None
    return b

class HighLowView(ui.View):
    def __init__(self, timeout=30):
        super().__init__(timeout=timeout)
        self.choice = None

    @ui.button(label="Low (1-49)", style=discord.ButtonStyle.primary)
    async def low(self, interaction: discord.Interaction, button: ui.Button):
        self.choice = "low"
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @ui.button(label="High (50-100)", style=discord.ButtonStyle.success)
    async def high(self, interaction: discord.Interaction, button: ui.Button):
        self.choice = "high"
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

class GamblingCommands(app_commands.Group):
    def __init__(self):
        super().__init__(name="gambling", description="Gambling related commands")

    # @safe_command(timeout=10.0)
    @app_commands.command(name="slots", description="Spin the slot machine and test your luck!")
    @cooldown(cl=15, tm=35.0, ft=3) # careful, discord rate limits edits to 5 per 5 seconds so we can't be too fast or we get fucked
    async def slots(self, interaction: discord.Interaction, bet_input: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        bet_amount = resolve_bet_input(bet_input, user_id)
        if bet_amount is None:
            return await interaction.followup.send("❌ Invalid bet amount (must be positive integer and ≤ your balance, or `*` for all-in).", ephemeral=True)
        bet = bet_amount
        balance = get_balance(user_id)

        symbols = ["🍒", "🍋", "🍉", "⭐", "🍌", "🍑", "🥭", "7️⃣", "🗿"]
        empty = "<:empty:1388238752295555162>"  # Replace with your actual :empty: emoji ID

        # Prepare the final result for the middle row
        final_row = random.choices(symbols, k=3)
        top_final = random.choices(symbols, k=3)
        bot_final = random.choices(symbols, k=3)

        # Animation setup: all reels start spinning
        spin_time = [2.5, 3.6, 4.5]  # seconds for each reel to stop
        interval = 0.38 # Less spammy, lower = faster updates but risks rate limit
        elapsed = 0
        start_time = asyncio.get_event_loop().time()
        stopped = [False, False, False]
        current = [[random.choice(symbols) for _ in range(3)] for _ in range(3)]  # 3 rows

        embed = discord.Embed(title="Slot Machine", color=0xFFD700)
        await interaction.followup.send(embed=embed, ephemeral=False)
        msg = await interaction.original_response()

        while not all(stopped):
            now = asyncio.get_event_loop().time()
            elapsed = now - start_time

            for col in range(3):
                if not stopped[col]:
                    # Update all 3 rows in this column with the same emoji
                    emoji = random.choice(symbols)
                    for row in range(3):
                        current[row][col] = emoji

            # Stop each reel at its time, and set its column to the final result
            for col, t in enumerate(spin_time):
                if not stopped[col] and elapsed >= t:
                    stopped[col] = True
                    current[0][col] = top_final[col]
                    current[1][col] = final_row[col]
                    current[2][col] = bot_final[col]

            # Build the slot matrix
            matrix = (
                f"{empty} {current[0][0]} {current[0][1]} {current[0][2]} {empty}\n"
                f"➡️ {current[1][0]} {current[1][1]} {current[1][2]} ⬅️\n"
                f"{empty} {current[2][0]} {current[2][1]} {current[2][2]} {empty}"
            )
            embed.description = f"🎰{empty}🎰{empty}🎰\n{matrix}\n🎰{empty}🎰{empty}🎰\n*Spinning...*"
            await msg.edit(embed=embed)
            await asyncio.sleep(interval)

        # Final display
        matrix = (
            f"{empty} {top_final[0]} {top_final[1]} {top_final[2]} {empty}\n"
            f"➡️ {final_row[0]} {final_row[1]} {final_row[2]} ⬅️\n"
            f"{empty} {bot_final[0]} {bot_final[1]} {bot_final[2]} {empty}"
        )

        # Determine winnings (middle row only)
        slot1, slot2, slot3 = final_row
        winnings = 0
        if slot1 == slot2 == slot3:
            if slot1 == "7️⃣":
                winnings = bet * 10
            elif slot1 == "🗿":
                winnings = bet * 100
            else:
                winnings = bet * 5
        elif slot1 == slot2 or slot2 == slot3 or slot1 == slot3:
            winnings = bet * 2

        update_balance(user_id, winnings - bet)
        result = f"🎰{empty}🎰{empty}🎰\n{matrix}\n🎰{empty}🎰{empty}🎰\n"

        if winnings > 0:
            result += f"✨ **You won `{winnings}` coins!** ✨"
        else:
            result += "💀 **You lost your bet...**"

        embed.description = result
        await msg.edit(embed=embed)

    # @safe_command(timeout=10.0)
    @app_commands.command(name="roulette", description="Bet on a number or color (red/black) in Roulette!")
    @cooldown(cl=15, tm=35.0, ft=3)
    async def roulette(self, interaction: discord.Interaction, bet_input: str, choice: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        bet_amount = resolve_bet_input(bet_input, user_id)
        if bet_amount is None:
            return await interaction.followup.send("❌ Invalid bet amount (must be positive integer and ≤ your balance, or `*` for all-in).", ephemeral=True)
        bet = bet_amount
        balance = get_balance(user_id)

        wheel_numbers = list(range(0, 37))  # 0-36

        # Animation: show spinning effect before revealing result
        spin_steps = 8
        fake_spins = [random.choice(wheel_numbers) for _ in range(spin_steps - 1)]
        embed = discord.Embed(
            title="Roulette",
            description="🎡 Spinning the wheel...",
            color=0xFF4500
        )
        await interaction.followup.send(embed=embed, ephemeral=False)
        msg = await interaction.original_response()

        for i, num in enumerate(fake_spins):
            color = "🔴 Red" if num % 2 == 1 else "⚫ Black"
            embed.description = f"🎡 The ball is spinning... `{num}` {color}"
            await msg.edit(embed=embed)
            await asyncio.sleep(0.5 + i * 0.05)  # Slightly increase delay for effect

        # Now pick the real result
        landed = random.choice(wheel_numbers)
        color = "🔴 Red" if landed % 2 == 1 else "⚫ Black"

        winnings = 0
        if choice.isdigit():
            choice_num = int(choice)
            if choice_num == landed:
                winnings = bet * 35  # Single number payout
        elif choice.lower() in ["red", "black"]:
            if (choice.lower() == "red" and color == "🔴 Red") or (choice.lower() == "black" and color == "⚫ Black"):
                winnings = bet * 2

        update_balance(user_id, winnings - bet)
        result = f"🎡 The ball landed on `{landed}` {color}!\n"

        if winnings > 0:
            result += f"✨ **You won `{winnings}` coins!** ✨"
        else:
            result += "💀 **You lost your bet...**"

        embed.description = result
        await msg.edit(embed=embed)

    # @safe_command(timeout=20.0)
    @app_commands.command(name="blackjack", description="Play a game of Blackjack!")
    @cooldown(cl=20, tm=35.0, ft=3)
    async def blackjack(self, interaction: discord.Interaction, bet_input: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        bet_amount = resolve_bet_input(bet_input, user_id)
        if bet_amount is None:
            return await interaction.followup.send("❌ Invalid bet amount (must be positive integer and ≤ your balance, or `*` for all-in).", ephemeral=True)
        bet = bet_amount
        balance = get_balance(user_id)

        # Deck using ranks so we can display face cards as "10 (J/Q/K)" and Ace as "11 or 1 (A)"
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        deck = ranks * 4
        random.shuffle(deck)
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]

        def card_value(card):
            if card in ('J', 'Q', 'K'):
                return 10
            if card == 'A':
                return 11
            return int(card)

        def hand_value(hand):
            value = sum(card_value(c) for c in hand)
            aces = sum(1 for c in hand if c == 'A')
            while value > 21 and aces:
                value -= 10
                aces -= 1
            return value

        def card_display(card):
            if card in ('J', 'Q', 'K'):
                return f"10 ({card})"
            if card == 'A':
                return "11/1 (A)"
            return card

        def hand_str(hand):
            return ', '.join(card_display(card) for card in hand)

        # Show initial hands
        embed = discord.Embed(
            title="🃏 Blackjack",
            description=(
                f"**Your hand:** {hand_str(player)} (Total: {hand_value(player)})\n"
                f"**Dealer's hand:** {card_display(dealer[0])}, ?\n\n"
                "Type `hit` to draw another card or `stand` to hold. (3 min timeout)"
            ),
            color=0x008000
        )
        await interaction.followup.send(embed=embed, ephemeral=False)
        message = await interaction.original_response()

        # Player turn loop
        timed_out = False
        while True:
            def check(m):
                return (
                    m.author.id == user_id and
                    m.channel == interaction.channel and
                    m.content.lower() in ["hit", "stand"]
                )
            try:
                reply = await interaction.client.wait_for("message", timeout=180, check=check)
            except asyncio.TimeoutError:
                timed_out = True
                break

            if reply.content.lower() == "hit":
                player.append(deck.pop())
                if hand_value(player) > 21:
                    break
                # Update hand after hit
                embed = discord.Embed(
                    title="🃏 Blackjack",
                    description=(

                        f"**Your hand:** {hand_str(player)} (Total: {hand_value(player)})\n"
                        f"**Dealer's hand:** {card_display(dealer[0])}, ?\n\n"
                        "Type `hit` to draw another card or `stand` to hold. (3 min timeout)"
                    ),
                    color=0x008000
                )
                await interaction.followup.send(embed=embed)
            elif reply.content.lower() == "stand":
                break

        # If timed out
        if timed_out:
            try:
                update_balance(user_id, bet)
            except Exception:
                pass

            embed = discord.Embed(
                title="🃏 Blackjack",
                description=f"⏰ **Floor! Clock on {interaction.user}.** (Timed out)",
                color=0xFF0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        player_val = hand_value(player)

        # Dealer turn (standard: hit until 17 or more)
        while hand_value(dealer) < 17:
            dealer.append(deck.pop())
        dealer_val = hand_value(dealer)

        # Determine result
        if player_val > 21:
            result = "💀 **You busted! Dealer wins.**"
            update_balance(user_id, -bet)
        elif dealer_val > 21 or player_val > dealer_val:
            result = f"✨ **You win `{bet * 2}` coins!** ✨"
            update_balance(user_id, bet)  # Net gain is bet (total returned is 2x bet)
        elif player_val < dealer_val:
            result = "💀 **Dealer wins!**"
            update_balance(user_id, -bet)
        else:
            result = "⚖️ **It's a tie! You get your bet back.**"
            update_balance(user_id, 0)  # No change, bet returned

        embed = discord.Embed(
            title="🃏 Blackjack",
            description=(

                f"**Your hand:** {hand_str(player)} (Total: {player_val})\n"
                f"**Dealer's hand:** {hand_str(dealer)} (Total: {dealer_val})\n\n"
                f"{result}"
            ),
            color=0x008000
        )
        await interaction.followup.send(embed=embed, ephemeral=False)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="coinflip", description="Flip a coin and guess the outcome!")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def coinflip(self, interaction: discord.Interaction, bet_input: str, guess: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        bet_amount = resolve_bet_input(bet_input, user_id)
        if bet_amount is None:
            return await interaction.followup.send("❌ Invalid bet amount (must be positive integer and ≤ your balance, or `*` for all-in).", ephemeral=True)
        bet = bet_amount
        balance = get_balance(user_id)

        if guess.lower() not in ["heads", "tails"]:
            return await interaction.followup.send("❌ Invalid guess! Choose 'heads' or 'tails'.", ephemeral=True)

        result = random.choice(["heads", "tails"])
        winnings = bet * 2 if guess.lower() == result else -bet
        update_balance(user_id, winnings)

        embed = discord.Embed(
            title="🪙 Coin Flip",
            description=f"**You guessed:** {guess.capitalize()}\n**Result:** {result.capitalize()}\n",
            color=0xFFD700
        )
        
        if winnings > 0:
            embed.description += f"✨ **You won `{winnings}` coins!** ✨"
        else:
            embed.description += "💀 **You lost your bet...**"

        await interaction.followup.send(embed=embed, ephemeral=False)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="war", description="Play War! Higher card wins.")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def war(self, interaction: discord.Interaction, bet_input: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        bet_amount = resolve_bet_input(bet_input, user_id)
        if bet_amount is None:
            return await interaction.followup.send("❌ Invalid bet amount (must be positive integer and ≤ your balance, or `*` for all-in).", ephemeral=True)
        bet = bet_amount
        balance = get_balance(user_id)

        deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]  # 11=J, 12=Q, 13=K, 14=A
        player_card = random.choice(deck)
        dealer_card = random.choice(deck)

        card_names = {11: "J", 12: "Q", 13: "K", 14: "A"}
        def card_str(val):
            return card_names.get(val, str(val))

        embed = discord.Embed(
            title="🃏 War!",
            description="Drawing cards...",
            color=0x8B0000
        )
        await interaction.followup.send(embed=embed, ephemeral=False)
        msg = await interaction.original_response()

        await asyncio.sleep(1.5)
        embed.description = f"**You drew:** {card_str(player_card)}\n**Dealer drew:** ..."

        await msg.edit(embed=embed)
        await asyncio.sleep(1.5)
        embed.description = f"**You drew:** {card_str(player_card)}\n**Dealer drew:** {card_str(dealer_card)}"
        await msg.edit(embed=embed)
        await asyncio.sleep(0.8)

        if player_card > dealer_card:
            winnings = bet
            result = f"✨ **You win `{bet * 2}` coins!** ✨"
        elif player_card < dealer_card:
            winnings = -bet
            result = "💀 **Dealer wins!**"
        else:
            winnings = 0
            result = "⚖️ **It's a tie! Your bet is returned.**"

        update_balance(user_id, winnings)
        embed.description += f"\n\n{result}"
        await msg.edit(embed=embed)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="highlow", description="Bet on high (50-100) or low (1-49)!")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def dice(self, interaction: discord.Interaction, bet_input: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        bet_amount = resolve_bet_input(bet_input, user_id)
        if bet_amount is None:
            return await interaction.followup.send("❌ Invalid bet amount (must be positive integer and ≤ your balance, or `*` for all-in).", ephemeral=True)
        bet = bet_amount
        balance = get_balance(user_id)

        embed = discord.Embed(
            title="🎲 High/Low Dice",
            description="Choose **Low (1-49)** or **High (50-100)**!",
            color=0x7289DA
        )
        view = HighLowView()
        await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        msg = await interaction.original_response()

        timeout = await view.wait()
        if view.choice is None:
            try:
                update_balance(user_id, bet)
            except Exception:
                pass

            embed.description = f"⏰ Floor! Clock on {interaction.user} (Timed out) - Your bet returned."
            await msg.edit(embed=embed, view=None)
            return

        await asyncio.sleep(0.6)
        rolled = random.randint(1, 100)
        if (view.choice == "low" and rolled <= 49) or (view.choice == "high" and rolled >= 50):
            winnings = bet
            result = f"🎲 You rolled **{rolled}**!\n✨ **You win `{bet * 2}` coins!** ✨"
        else:
            winnings = -bet
            result = f"🎲 You rolled **{rolled}**!\n💀 **You lost your bet...**"

        update_balance(user_id, winnings)
        embed.description = result
        await msg.edit(embed=embed, view=None)

class GamblingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(GamblingCommands())

async def setup(bot):
    await bot.add_cog(GamblingCog(bot))