# database/items.py
# Centralized definition of all shop items, making stats easily tweakable.

SHOP_ITEMS = [
    {'id': 1, 'name': 'Bragging Rights', 'price': 10000, 'effect': 'Nothing. Just flex.', 'uses_left': 1},
    {'id': 2, 'name': 'Financial Drain', 'price': 5000, 'effect': 'Drains one percent of your balance per hour, I wonder where that money goes..', 'uses_left': 1},
    {'id': 3, 'name': 'Bolt Cutters', 'price': 3000, 'effect': 'Improves robbery success', 'uses_left': 4},
    {'id': 4, 'name': 'Padlocked Wallet', 'price': 2000, 'effect': 'Protects against robbery', 'uses_left': 10},
    {'id': 5, 'name': 'Taser', 'price': 3500, 'effect': 'Stuns robbers', 'uses_left': 2},
    {'id': 6, 'name': 'Lucky Coin', 'price': 1500, 'effect': 'Boosts gambling odds.. or just a really expensive paperweight', 'uses_left': 4},
    {'id': 7, 'name': 'VIP Pass', 'price': 50000, 'effect': 'Grants VIP access', 'uses_left': 1},
    {'id': 8, 'name': 'Hackatron 9900', 'price': 7000, 'effect': 'Increases heist efficiency', 'uses_left': 5},
    {'id': 9, 'name': 'Resintantoinem Sample', 'price': 4000, 'effect': "Probaably a bad idea, increases heist efficiency but once effect wears off you'll be more susceptible", 'uses_left': 1},
    {'id': 10, 'name': 'Loaded Gun', 'price': 9000, 'effect': 'You remembered your 2nd amendment rights, self defense agaist robbers', 'uses_left': 19},
    {'id': 11, 'name': 'Watermelon', 'price': 500, 'effect': 'Doctors approve! Does nothing', 'uses_left': 500},
    {'id': 12, 'name': 'Rubbers', 'price': 5, 'effect': 'A cheap rubber band. Absolutely useless, but great for spamming your friends.', 'uses_left': 1},
    {'id': 13, 'name': 'Coffee Mug (Full)', 'price': 800, 'effect': 'A warm mug of coffee. Keeps you alert, reducing robbing success against you by 30%.', 'uses_left': 5},
    {'id': 14, 'name': 'Sea BassHead', 'price': 2200, 'effect': 'A strange fish variant that mutters calculations. Grants a 25% chance to save you from a gambling loss.', 'uses_left': 8},
    {'id': 15, 'name': 'Lint', 'price': 2, 'effect': 'Just some pocket lint. Truly miscellaneous.', 'uses_left': 1},
    {'id': 16, 'name': 'Fidget Spinner', 'price': 15, 'effect': 'A relic from 2017. Spins fast, does nothing.', 'uses_left': 10},
    {'id': 17, 'name': 'Fake Gold Bar', 'price': 100, 'effect': 'A gold-painted plastic bar. Looks heavy, but it is fake.', 'uses_left': 1},
    {'id': 18, 'name': 'Melted Ice Cream', 'price': 10, 'effect': 'It was once delicious. Now it is a sticky mess.', 'uses_left': 1},
    {'id': 19, 'name': 'Screwdriver', 'price': 150, 'effect': 'A rusty flathead screwdriver. Good for tightening loose screws.', 'uses_left': 5},
    {'id': 20, 'name': 'Pocket sand', 'price': 400, 'effect': 'Throw sand in their eyes! Defends against robbery and temporarily bans the robber from robbing for 3 hours.', 'uses_left': 3},
    {'id': 21, 'name': 'USB Stick', 'price': 1200, 'effect': 'Cheat codes! Boosts casino win chances by 35%, but has a slight chance of backfiring and getting you arrested for 30 mins.', 'uses_left': 5},
    {'id': 22, 'name': 'Piss Bottle', 'price': 300, 'effect': 'Throw at someone to disorient them. Increases your robbery chance against them by 25% for 1 hour. Use: /shop use "Piss Bottle" @target', 'uses_left': 2},
    {'id': 23, 'name': 'Half eaten rotisserie chicken', 'price': 200, 'effect': 'Greasy carcass with wishbone in. May randomly increase or decrease your gambling odds on each bet.', 'uses_left': 3},
    {'id': 24, 'name': 'Parking cone', 'price': 600, 'effect': 'Defends against robbery. Puts a cone on the robber, failing the robbery and reducing their rob success chance by 30% for 1 hour.', 'uses_left': 2},
    {'id': 25, 'name': 'An entire ikea', 'price': 250000, 'effect': 'How the fuck are you carrying that? A legendary, extremely expensive status symbol.', 'uses_left': 1},
    {'id': 26, 'name': 'Expired fish', 'price': 350, 'effect': 'wallet smells fishy, driving people away. Reduces robbing success against you by 15% automatically.', 'uses_left': 4},
    {'id': 27, 'name': 'Milk suds', 'price': 400, 'effect': 'Throw at someone. Victim will fail work, crime, and slut jobs 20% more often for 2 hours. Use: /shop use "Milk suds" @target', 'uses_left': 3}
]

def get_item_by_id(item_id: int):
    for item in SHOP_ITEMS:
        if item['id'] == item_id:
            return item
    return None

def get_all_items():
    return SHOP_ITEMS

ITEM_EFFECTS = {
    2: {"drain_percent": 1},
    3: {"robbery_modifier": 50},
    4: {"robbery_modifier": -50}, # Protective
    5: {"taser": True, "robbery_modifier": -100},
    6: {"gambling_placebo": True},
    8: {"robbery_modifier": 20},
    9: {"robbery_modifier": 50, "temporary_effect": True, "duration": 3600},
    10: {"gun_defense": True, "uses": 19},
    12: {"rubbers": True},
    13: {"robbery_modifier": -30, "coffee_defense": True, "uses": 5},
    14: {"gambling_boost": True, "uses": 8},
    15: {"misc": True},
    16: {"misc": True},
    17: {"misc": True},
    18: {"misc": True},
    19: {"misc": True},
    20: {"pocket_sand": True, "uses": 3},
    21: {"usb_stick": True, "uses": 5},
    22: {"piss_bottle": True, "uses": 2},
    23: {"chicken_wishbone": True, "uses": 3},
    24: {"parking_cone": True, "uses": 2},
    25: {"status_symbol": True},
    26: {"expired_fish": True, "uses": 4, "robbery_modifier": -15},
    27: {"milk_suds": True, "uses": 3}
}
