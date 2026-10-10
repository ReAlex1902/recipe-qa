"""The curated recipe list (52 pages).

Chosen from the Wikibooks categories Soup, Dessert, Vegetarian/Vegan, Italian, Indian and
Mexican recipes, for (SPEC AC6):
- variety of cuisines: Italian, Indian, Mexican, Thai, Austrian, Bulgarian, Polish,
  Nigerian, Algerian, ...;
- overlapping dishes that disagree: two carbonaras, three marinara sauces,
  two guacamoles, two risottos, two rice puddings;
- different levels of structure: full `Recipe summary` with time, pages without time,
  pages with prose instead of lists;
- constraint coverage: vegetarian/vegan dishes with stated times ≤ 30 minutes, and dishes
  with nuts, dairy, gluten and shellfish for allergy questions.

A curated list (not "first N of a category") keeps the corpus stable when Wikibooks
categories change. `--pinned` additionally pins the exact revisions.
"""

TITLES: tuple[str, ...] = (
    # Italian / pasta
    "Cookbook:Spaghetti alla Carbonara",
    "Cookbook:Carbonara Pasta",
    "Cookbook:Spaghetti alla Puttanesca",
    "Cookbook:Linguine Primavera Mediterranean",
    "Cookbook:Penne alla Casa (Penne in Creamy Tomato Sauce)",
    "Cookbook:Spaghetti with Bolognese Meat Sauce",
    "Cookbook:Spaghetti with Clams",
    "Cookbook:Marinara Sauce",
    "Cookbook:American Marinara Sauce I",
    "Cookbook:American Marinara Sauce II",
    "Cookbook:Pesto I",
    "Cookbook:Risotto (Basic)",
    "Cookbook:Tuscan Mushroom Risotto",
    "Cookbook:Bruschetta",
    "Cookbook:Zucchini Pasta Bake",
    "Cookbook:Basic Lasagna",
    "Cookbook:Tiramisù",
    "Cookbook:Italian Rice Pudding",
    # Indian
    "Cookbook:Paneer Butter Masala",
    "Cookbook:Mango and Yellow Split Pea Curry",
    "Cookbook:Malai Mixed Vegetable Curry",
    "Cookbook:Chicken Tikka Masala",
    "Cookbook:Potato Curry (Aloo Masala)",
    "Cookbook:Keralan Vegetable Stew",
    "Cookbook:Pohe (Spiced Flattened Rice)",
    "Cookbook:Egg Rice",
    "Cookbook:Kheer (Rice Pudding)",
    "Cookbook:Naan",
    # Mexican
    "Cookbook:Guacamole I",
    "Cookbook:Chunky Guacamole",
    "Cookbook:Salsa Fresca",
    "Cookbook:Churros I",
    "Cookbook:Mexican Tomato Soup",
    "Cookbook:Black Bean Soup with Tomato-Tomatillo Salsa",
    # Soups from other cuisines
    "Cookbook:Tarator (Bulgarian Cucumber Soup)",
    "Cookbook:Tom Yum Gai",
    "Cookbook:Zuppa Toscana",
    "Cookbook:Tuscan Soup (Meatless)",
    "Cookbook:Vegetable Soup",
    "Cookbook:White Bean Soup with Basil, Rosemary, and Garlic Croutons",
    "Cookbook:Sweet Potato Soup",
    "Cookbook:Yellow Split Pea Soup",
    "Cookbook:Zupa Ogórkowa (Polish Cucumber Soup)",
    "Cookbook:Algerian White Chicken Vermicelli Soup (Chorba Beida)",
    "Cookbook:Ofe Onugbu (Bitter Leaf Soup)",
    # Desserts
    "Cookbook:Rhubarb Pie",
    "Cookbook:Sachertorte",
    "Cookbook:Vanillekipferl (Almond Crescent Cookies)",
    "Cookbook:Snickerdoodles IV",
    "Cookbook:White Chocolate Cheesecake",
    "Cookbook:Stroopwafels",
    "Cookbook:Simple Chocolate Fudge",
)
