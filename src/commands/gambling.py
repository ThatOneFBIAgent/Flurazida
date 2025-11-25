# Standard Library Imports
import asyncio
import random

# Third-Party Imports
import discord
from discord.ext import commands
from discord import app_commands, Interaction, ui
from discord.ui import Button, View

# Local Imports
from database import update_balance, get_balance
from config import cooldown
from logger import get_logger

log = get_logger()

DEBT_FLOOR = -1000  # Minimum allowed balance

class CrashBetModal(ui.Modal, title="Place your bet"):
    def __init__(self, players_dict, user_id):
        super().__init__()
        self.players = players_dict
        self.user_id = user_id
        self.bet = ui.TextInput(label="Bet Amount", placeholder="Enter how many coins you want to wager", required=True)
        self.add_item(self.bet)

    async def on_submit(self, interaction: Interaction):
        user_id = interaction.user.id
        balance = await get_balance(user_id)
        
        # Check if user already joined
        if user_id in self.players:
            return await interaction.response.send_message("❌ You have already placed a bet!", ephemeral=True)

        bet_val = await resolve_bet_input(self.bet.value, user_id)
        
        if bet_val is None or bet_val <= 0:
            return await interaction.response.send_message("❌ Invalid bet amount!", ephemeral=True)

        if bet_val > balance:
             return await interaction.response.send_message(f"❌ You don't have enough coins! (Balance: {balance})", ephemeral=True)

        await update_balance(user_id, -bet_val)
        self.players[user_id] = bet_val
        await interaction.response.send_message(f"✅ Bet of `{bet_val}` coins accepted!", ephemeral=True)

class PlayAgainView(ui.View):
    def __init__(self, callback, user_id, *args, **kwargs):
        super().__init__(timeout=90)
        self.callback = callback
        self.user_id = user_id
        self.args = args
        self.kwargs = kwargs

    @ui.button(label="🔄 Play Again", style=discord.ButtonStyle.primary)
    async def play_again(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("🚫 This isn't your game!", ephemeral=True)
        
        # Disable button and stop the view to prevent further clicks
        button.disabled = True
        self.stop()
        
        # Update the message to show the button is disabled (don't respond yet!)
        try:
            await interaction.message.edit(view=self)
        except:
            pass  # Message might be deleted or inaccessible
        
        # Run the callback with the new interaction (callback will respond)
        await self.callback(interaction, *self.args, **self.kwargs)
    
    async def on_timeout(self):
        """Disable the button when the view times out."""
        # Disable all buttons
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True
        
        # Try to edit the message to show disabled button
        try:
            if hasattr(self, 'message') and self.message:
                await self.message.edit(view=self)
        except:
            pass  # Message might be deleted or we don't have permission

class HighLowView(ui.View):
    def __init__(self, user_id, bet, current_card, callback):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.bet = bet
        self.current_card = current_card
        self.callback = callback

    async def end_game(self, interaction, won, next_card):
        if won:
            win = self.bet
            await update_balance(self.user_id, win)
            result = f"✨ **Correct!** Next card was **{next_card}**. You won `{win}` coins! ✨"
            color = 0x00FF00
        else:
            await update_balance(self.user_id, -self.bet)
            result = f"❌ **Wrong!** Next card was **{next_card}**. You lost `{self.bet}` coins."
            color = 0xFF0000
            
        embed = interaction.message.embeds[0]
        embed.description = f"Card was **{self.current_card}**. Next was **{next_card}**.\n\n{result}"
        embed.color = color
        
        # Add Play Again button
        self.clear_items()
        self.add_item(PlayAgainView(self.callback, self.user_id, self.bet).children[0])
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Higher ⬆️", style=discord.ButtonStyle.success)
    async def higher(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("🚫 Not your game!", ephemeral=True)
        
        next_card = random.randint(1, 13)
        won = next_card >= self.current_card
        await self.end_game(interaction, won, next_card)

    @ui.button(label="Lower ⬇️", style=discord.ButtonStyle.danger)
    async def lower(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("🚫 Not your game!", ephemeral=True)
        
        next_card = random.randint(1, 13)
        won = next_card <= self.current_card
        await self.end_game(interaction, won, next_card)

async def resolve_bet_input(bet_input, user_id):
    """
    Accepts a user-provided bet (string or int).
    Supports "*", "all", "max", "allin" (case-insensitive) for all-in.
    Returns an int bet on success, or None on invalid.
    """
    bal = await get_balance(user_id)
    if bal <= DEBT_FLOOR:
        return None  # Can't bet while at or below floor

    if isinstance(bet_input, int):
        b = bet_input
    else:
        s = str(bet_input).strip().lower()
        if s in ("*", "all", "max", "allin", "all-in"):
            # For debt or low balances, all-in = whatever positive funds remain
            return max(0, bal)
        try:
            b = int(s)
        except ValueError:
            return None

    if b <= 0:
        return None
    if b > max(0, bal):  # Can't bet more than available positive funds
        return None
    return b

class GamblingCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="gambling", description="Gambling commands")
        self.bot = bot
        self.active_crash_games = {}

    class BlackjackView(ui.View):
        def __init__(self, user_id, bet, deck, player_hand, dealer_hand, embed, message):
            super().__init__(timeout=180)
            self.user_id = user_id
            self.bet = bet
            self.deck = deck
            self.player = player_hand
            self.dealer = dealer_hand
            self.embed = embed
            self.message = message
            self.ended = False

        def card_value(self, card):
            if card in ('J', 'Q', 'K'): return 10
            if card == 'A': return 11
            return int(card)

        def hand_value(self, hand):
            value = sum(self.card_value(c) for c in hand)
            aces = sum(1 for c in hand if c == 'A')
            while value > 21 and aces:
                value -= 10
                aces -= 1
            return value

        def card_display(self, card):
            if card in ('J', 'Q', 'K'): return f"10 ({card})"
            if card == 'A': return "11/1 (A)"
            return str(card)

        def hand_str(self, hand):
            return ', '.join(self.card_display(card) for card in hand)

        async def update_embed(self, result=None):
            player_val = self.hand_value(self.player)
            dealer_val = self.hand_value(self.dealer)
            
            desc = f"**Your hand:** {self.hand_str(self.player)} (Total: {player_val})\n"
            if result:
                desc += f"**Dealer's hand:** {self.hand_str(self.dealer)} (Total: {dealer_val})\n\n{result}"
            else:
                desc += f"**Dealer's hand:** {self.card_display(self.dealer[0])}, ?\n\nType `hit` or `stand` via buttons."

            self.embed.description = desc
            if result:
                self.embed.color = 0xFF0000 if "lost" in result or "busted" in result else (0x00FF00 if "win" in result else 0xFFFF00)
                self.clear_items()
                # Add Play Again button
                self.add_item(PlayAgainView(self.start_new_game, self.user_id, self.bet).children[0])
            
            await self.message.edit(embed=self.embed, view=self)

        async def start_new_game(self, interaction, bet):
            pass 

        @ui.button(label="Hit", style=discord.ButtonStyle.success)
        async def hit(self, interaction: discord.Interaction, button: ui.Button):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("🚫 This isn't your game!", ephemeral=True)
            
            await interaction.response.defer()
            self.player.append(self.deck.pop())
            if self.hand_value(self.player) > 21:
                self.ended = True
                await update_balance(self.user_id, -self.bet)
                await self.update_embed("💀 **You busted! Dealer wins.**")
                self.stop()
            else:
                await self.update_embed()

        @ui.button(label="Stand", style=discord.ButtonStyle.danger)
        async def stand(self, interaction: discord.Interaction, button: ui.Button):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("🚫 This isn't your game!", ephemeral=True)
            
            await interaction.response.defer()
            self.ended = True
            
            # Dealer turn
            while self.hand_value(self.dealer) < 17:
                self.dealer.append(self.deck.pop())
            
            player_val = self.hand_value(self.player)
            dealer_val = self.hand_value(self.dealer)

            if dealer_val > 21 or player_val > dealer_val:
                await update_balance(self.user_id, self.bet)
                result = f"✨ **You win `{self.bet * 2}` coins!** ✨"
            elif player_val < dealer_val:
                await update_balance(self.user_id, -self.bet)
                result = "💀 **Dealer wins!**"
            else:
                await update_balance(self.user_id, 0)
                result = "⚖️ **It's a tie! You get your bet back.**"
                
            await self.update_embed(result)
            self.stop()

        async def on_timeout(self):
            if not self.ended:
                try:
                    await update_balance(self.user_id, self.bet) 
                except: pass
                self.embed.description = f"⏰ **Floor! Clock on {self.user_id}.** (Timed out)"
                self.embed.color = 0xFF0000
                self.clear_items()
                await self.message.edit(embed=self.embed, view=None)

    @app_commands.command(name="blackjack", description="Play a game of Blackjack!")
    @cooldown(cl=20, tm=200.0, ft=3)
    async def blackjack(self, interaction: discord.Interaction, bet_input: str):
        user_id = interaction.user.id
        bet = await resolve_bet_input(bet_input, user_id)
        if bet is None or bet <= 0:
            return await interaction.response.send_message("❌ Invalid bet amount.", ephemeral=True)
        
        await self.run_blackjack(interaction, bet)

    async def run_blackjack(self, interaction: discord.Interaction, bet: int):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        
        balance = await get_balance(user_id)
        if balance <= DEBT_FLOOR:
             return await interaction.followup.send(f'💸 You are too deep in debt ({balance} coins)! No gambling.', ephemeral=True)
        if bet > max(0, balance):
             return await interaction.followup.send(f'❌ You don\'t have enough coins for this bet! (Balance: {balance})', ephemeral=True)

        # Deck setup
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        deck = ranks * 4
        random.shuffle(deck)
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]

        def card_display(card):
            if card in ('J', 'Q', 'K'): return f"10 ({card})"
            if card == 'A': return "11/1 (A)"
            return str(card)
        
        def hand_value(hand):
            def val(c):
                if c in ('J', 'Q', 'K'): return 10
                if c == 'A': return 11
                return int(c)
            value = sum(val(c) for c in hand)
            aces = sum(1 for c in hand if c == 'A')
            while value > 21 and aces:
                value -= 10
                aces -= 1
            return value

        embed = discord.Embed(
            title="🃏 Blackjack",
            description=(
                f"**Your hand:** {', '.join(card_display(c) for c in player)} (Total: {hand_value(player)})\n"
                f"**Dealer's hand:** {card_display(dealer[0])}, ?\n\n"
                "Type `hit` or `stand` via buttons."
            ),
            color=0x008000
        )
        
        # Send initial message with embed but no view yet
        await interaction.followup.send(embed=embed)
        message = await interaction.original_response()
        
        # Instantiate the view and link it to the message
        view = self.BlackjackView(user_id, bet, deck, player, dealer, embed, message)
        # Hacky way to pass the callback for Play Again
        view.start_new_game = self.run_blackjack
        
        await message.edit(view=view)

    async def run_slots(self, interaction: discord.Interaction, bet: int):
        await interaction.response.defer(ephemeral=False)

        user_id = interaction.user.id
        balance = await get_balance(user_id)

        if balance <= DEBT_FLOOR:
            return await interaction.followup.send(
                f'💸 You are too deep in debt ({balance} coins)! No gambling.',
                ephemeral=True
            )

        if bet > max(0, balance):
            return await interaction.followup.send(
                f'❌ You don\'t have enough coins for this bet! (Balance: {balance})',
                ephemeral=True
            )

        PAYOUT = {
            # commons
            "🍒": {"3": 1.2, "2": 0.8},
            "🍋": {"3": 1.2, "2": 0.8},
            "🍇": {"3": 1.2, "2": 0.8},

            # rares
            "🍉": {"3": 3.0, "2": 1.2},
            "🍓": {"3": 3.0, "2": 1.2},
            "🍍": {"3": 3.0, "2": 1.2},

            # epics
            "🔔": {"3": 10, "2": 3},
            "7️⃣": {"3": 10, "2": 3},

            # legendaries
            "💎": {"3": 50, "2": 10},

            # how the FUCK
            "🗿": {"3": 100, "2": 25}
        }

        # Weighted emojis
        emojis = ["🍒","🍋","🍇","🍉","🍓","🍍","7️⃣","🔔","💎","🗿"]
        weights = [100, 100, 100, 35, 35, 35, 8, 8, 2, 1] # numbers are big because of the way the random.choices() works

        # Build a fresh 3x3 grid
        def generate_grid():
            return [
                [random.choices(emojis, weights)[0] for _ in range(3)]
                for _ in range(3)
            ]

        # suspense baby
        suspense_msg = await interaction.followup.send("🎰 Spinning...")
        await asyncio.sleep(1.2)
        await suspense_msg.edit(content="🎰 Spinning... *click*")
        await asyncio.sleep(1)
        await suspense_msg.edit(content="🎰 Spinning... *click click*")
        await asyncio.sleep(1)

        grid = generate_grid()

        # check matches
        three_match_bonus = 0
        two_match_bonus = 0

        def evaluate_spin(grid):
            payouts = {}  # symbol -> best multiplier

            lines = [
                grid[0], grid[1], grid[2], # rows
                [grid[0][0], grid[1][0], grid[2][0]], # col 1
                [grid[0][1], grid[1][1], grid[2][1]], # col 2
                [grid[0][2], grid[1][2], grid[2][2]]  # col 3
            ]
            lines.append([grid[0][0], grid[1][1], grid[2][2]])
            lines.append([grid[0][2], grid[1][1], grid[2][0]])

            for line in lines:
                a, b, c = line
                symbols = [a, b, c]

                # triple
                if a == b == c:
                    mult = PAYOUT[a]["3"]
                    payouts[a] = max(payouts.get(a, 0), mult)
                    continue

                # double (if allowed)
                for sym in set(symbols):
                    if symbols.count(sym) == 2:
                        mult = PAYOUT[sym]["2"]
                        payouts[sym] = max(payouts.get(sym, 0), mult)

            # sum only best payouts
            total = sum(payouts.values())
            return total

        # payout logic
        win_amount = 0
        lose_amount = bet

        # final balance update
        total_multiplier = evaluate_spin(grid)

        win_amount = int(bet * total_multiplier)
        net = win_amount - bet
        await update_balance(user_id, net)

        # build lines for display
        formatted_grid = "\n".join(
            f"| {grid[r][0]} | {grid[r][1]} | {grid[r][2]} |"
            for r in range(3)
        )

        # fancy result message
        if net > 0:
            msg = f"🎉 You netted `+{abs(net)}` coins!"
            color = 0xF1C40F
        elif net == 0:
            msg = f"😐 You broke even. Not bad, not great."
            color = 0x7289DA
        else:
            msg = f"❌ You lost `{abs(net)}` coins."
            color = 0xFF0000

        embed = discord.Embed(
            title="🎰 Slots",
            description=f"```\n{formatted_grid}\n```\n{msg}",
            color=color
        )

        view = PlayAgainView(self.run_slots, user_id, bet)
        await suspense_msg.edit(content=None, embed=embed, view=view)

    @app_commands.command(name="slots", description="Spin the slots!")
    @cooldown(cl=5, tm=200.0, ft=3)
    async def slots(self, interaction: discord.Interaction, bet_input: str):
        user_id = interaction.user.id
        bet = await resolve_bet_input(bet_input, user_id)
        if bet is None or bet <= 0:
            return await interaction.response.send_message("❌ Invalid bet amount.", ephemeral=True)
        await self.run_slots(interaction, bet)

    async def run_coinflip(self, interaction: discord.Interaction, bet: int, choice: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        
        balance = await get_balance(user_id)
        if balance <= DEBT_FLOOR:
             return await interaction.followup.send(f'💸 You are too deep in debt ({balance} coins)! No gambling.', ephemeral=True)
        if bet > max(0, balance):
             return await interaction.followup.send(f'❌ You don\'t have enough coins for this bet! (Balance: {balance})', ephemeral=True)

        outcome = random.choice(["heads", "tails"])
        won = choice.lower() == outcome
        
        if won:
            win = bet
            result = f"✨ It was **{outcome.title()}**! You won `{win}` coins! ✨"
            await update_balance(user_id, win)
        else:
            result = f"❌ It was **{outcome.title()}**. You lost `{bet}` coins."
            await update_balance(user_id, -bet)
            
        embed = discord.Embed(
            title="🪙 Coinflip",
            description=result,
            color=0xF1C40F if won else 0xFF0000
        )
        
        view = PlayAgainView(self.run_coinflip, user_id, bet, choice)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="coinflip", description="Flip a coin!")
    @app_commands.describe(choice="Heads or Tails")
    @cooldown(cl=5, tm=200.0, ft=3)
    async def coinflip(self, interaction: discord.Interaction, bet_input: str, choice: str):
        if choice.lower() not in ["heads", "tails"]:
            return await interaction.response.send_message("❌ Choice must be 'heads' or 'tails'.", ephemeral=True)
            
        user_id = interaction.user.id
        bet = await resolve_bet_input(bet_input, user_id)
        if bet is None or bet <= 0:
            return await interaction.response.send_message("❌ Invalid bet amount.", ephemeral=True)
        await self.run_coinflip(interaction, bet, choice)

    async def run_war(self, interaction: discord.Interaction, bet: int):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        
        balance = await get_balance(user_id)
        if balance <= DEBT_FLOOR:
             return await interaction.followup.send(f'💸 You are too deep in debt ({balance} coins)! No gambling.', ephemeral=True)
        if bet > max(0, balance):
             return await interaction.followup.send(f'❌ You don\'t have enough coins for this bet! (Balance: {balance})', ephemeral=True)

        player_card = random.randint(1, 13)
        dealer_card = random.randint(1, 13)
        
        def card_name(val):
            if val == 1: return "Ace"
            if val == 11: return "Jack"
            if val == 12: return "Queen"
            if val == 13: return "King"
            return str(val)

        if player_card > dealer_card:
            win = bet
            result = f"✨ **You won!** `{win}` coins! ✨"
            color = 0x00FF00
            await update_balance(user_id, win)
        elif player_card < dealer_card:
            result = f"❌ **You lost!** `{bet}` coins."
            color = 0xFF0000
            await update_balance(user_id, -bet)
        else:
            result = "⚖️ **It's a tie!** Bet returned."
            color = 0xFFFF00
            await update_balance(user_id, 0)
            
        embed = discord.Embed(
            title="⚔️ War",
            description=f"Your Card: **{card_name(player_card)}**\nDealer's Card: **{card_name(dealer_card)}**\n\n{result}",
            color=color
        )
        
        view = PlayAgainView(self.run_war, user_id, bet)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="war", description="Play a game of War (High card wins)")
    @cooldown(cl=5, tm=200.0, ft=3)
    async def war(self, interaction: discord.Interaction, bet_input: str):
        user_id = interaction.user.id
        bet = await resolve_bet_input(bet_input, user_id)
        if bet is None or bet <= 0:
            return await interaction.response.send_message("❌ Invalid bet amount.", ephemeral=True)
        await self.run_war(interaction, bet)

    async def run_highlow(self, interaction: discord.Interaction, bet: int):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        
        balance = await get_balance(user_id)
        if balance <= DEBT_FLOOR:
             return await interaction.followup.send(f'💸 You are too deep in debt ({balance} coins)! No gambling.', ephemeral=True)
        if bet > max(0, balance):
             return await interaction.followup.send(f'❌ You don\'t have enough coins for this bet! (Balance: {balance})', ephemeral=True)

        first_card = random.randint(1, 13)
        
        embed = discord.Embed(
            title="⬆️ High or Low ⬇️",
            description=f"Card is **{first_card}**. Will the next one be Higher or Lower?",
            color=0x3498DB
        )
        
        view = HighLowView(user_id, bet, first_card, self.run_highlow)
        await interaction.followup.send(embed=embed, view=view)
        if balance <= DEBT_FLOOR:
             return await interaction.followup.send(f'💸 You are too deep in debt ({balance} coins)! No gambling.', ephemeral=True)
        if bet > max(0, balance):
             return await interaction.followup.send(f'❌ You don\'t have enough coins for this bet! (Balance: {balance})', ephemeral=True)

        first_card = random.randint(1, 13)
        
        embed = discord.Embed(
            title="⬆️ High or Low ⬇️",
            description=f"Card is **{first_card}**. Will the next one be Higher or Lower?",
            color=0x3498DB
        )
        
        view = HighLowView(user_id, bet, first_card, self.run_highlow)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="highlow", description="Guess if the next card is higher or lower!")
    @cooldown(cl=5, tm=200.0, ft=3)
    async def highlow(self, interaction: discord.Interaction, bet_input: str):
        user_id = interaction.user.id
        bet = await resolve_bet_input(bet_input, user_id)
        if bet is None or bet <= 0:
            return await interaction.response.send_message("❌ Invalid bet amount.", ephemeral=True)
        await self.run_highlow(interaction, bet)


class GamblingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_crash_games = {}

    async def cog_load(self):
        self.bot.tree.add_command(GamblingCommands(self.bot))

async def setup(bot):
    await bot.add_cog(GamblingCog(bot))
