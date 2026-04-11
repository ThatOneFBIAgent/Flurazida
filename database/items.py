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
    {'id': 11, 'name': 'Watermelon', 'price': 500, 'effect': 'Doctors approve! Does nothing', 'uses_left': 500}
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
    10: {"gun_defense": True, "uses": 19}
}
