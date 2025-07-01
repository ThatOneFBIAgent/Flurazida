import discord, random, asyncio
from discord.ext import commands
from discord import app_commands
from discord import Interaction
from database import update_balance, get_balance
from config import cooldown


class Gambling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="slots", description="Spin the slot machine and test your luck!")
    @cooldown(8)
    async def slots(self, interaction: discord.Interaction, bet: int):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        balance = get_balance(user_id)
        if bet <= 0 or bet > balance:
            return await interaction.followup.send("❌ Invalid bet amount!", ephemeral=False)

        symbols = ["🍒", "🍋", "🍉", "⭐", "🍌", "🍑", "🥭", "7️⃣", "🗿"]
        empty = "<:empty:1388238752295555162>"  # Replace with your actual :empty: emoji ID

        # Prepare the final result for the middle row
        final_row = random.choices(symbols, k=3)
        top_final = random.choices(symbols, k=3)
        bot_final = random.choices(symbols, k=3)

        # Animation setup: all reels start spinning
        spin_time = [2.5, 3.6, 4.5]  # seconds for each reel to stop
        interval = 0.38 # Less spammy, lower = faster updates
        elapsed = 0
        start_time = asyncio.get_event_loop().time()
        stopped = [False, False, False]
        current = [[random.choice(symbols) for _ in range(3)] for _ in range(3)]  # 3 rows

        embed = discord.Embed(title="Slot Machine", color=0xFFD700)
        await interaction.followup.send(embed=embed)
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

    @app_commands.command(name="roulette", description="Bet on a number or color (red/black) in Roulette!")
    @cooldown(8)
    async def roulette(self, interaction: discord.Interaction, bet: int, choice: str):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        balance = get_balance(user_id)
        if bet <= 0 or bet > balance:
            return await interaction.followup.send("❌ Invalid bet amount!", ephemeral=False)

        wheel_numbers = list(range(0, 37))  # 0-36

        # Animation: show spinning effect before revealing result
        spin_steps = 8
        fake_spins = [random.choice(wheel_numbers) for _ in range(spin_steps - 1)]
        embed = discord.Embed(
            title="Roulette",
            description="🎡 Spinning the wheel...",
            color=0xFF4500
        )
        await interaction.followup.send(embed=embed)
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

    @app_commands.command(name="blackjack", description="Play a game of Blackjack!")
    @cooldown(20)
    async def blackjack(self, interaction: discord.Interaction, bet: int):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        balance = get_balance(user_id)
        if bet <= 0 or bet > balance:
            return await interaction.followup.send("❌ Invalid bet amount!", ephemeral=True)

        # Simple deck: 2-10, J, Q, K as 10, Ace as 11
        deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11] * 4
        random.shuffle(deck)
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]

        def hand_value(hand):
            value = sum(hand)
            aces = hand.count(11)
            while value > 21 and aces:
                value -= 10
                aces -= 1
            return value

        def hand_str(hand):
            return ', '.join(str(card) for card in hand)

        # Show initial hands
        embed = discord.Embed(
            title="🃏 Blackjack",
            description=(
                f"**Your hand:** {hand_str(player)} (Total: {hand_value(player)})\n"
                f"**Dealer's hand:** {dealer[0]}, ?\n\n"
                "Type `hit` to draw another card or `stand` to hold. (3 min timeout)"
            ),
            color=0x008000
        )
        await interaction.followup.send(embed=embed)
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
                        f"**Dealer's hand:** {dealer[0]}, ?\n\n"
                        "Type `hit` to draw another card or `stand` to hold. (3 min timeout)"
                    ),
                    color=0x008000
                )
                await interaction.followup.send(embed=embed)
            elif reply.content.lower() == "stand":
                break

        # If timed out
        if timed_out:
            embed = discord.Embed(
                title="🃏 Blackjack",
                description="⏰ **Timed out! Game cancelled.**",
                color=0xFF0000
            )
            await interaction.followup.send(embed=embed)
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
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Gambling(bot))