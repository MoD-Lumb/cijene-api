from service.routers.nutrition import parse_ingredients, parse_recipes

HEADER = "id,name,food_group,meal_role,state,kcal,protein,carbs,fat,grams_per_piece,ml_to_g,preference,source,note"


def test_comma_file():
    data = f"{HEADER}\njaje,Jaje,jaja,Proteini,raw,143,12.6,0.7,9.5,55,,green,USDA,\n"
    ingredients, warnings = parse_ingredients(data.encode("utf-8"))
    assert warnings == []
    assert ingredients[0].protein == 12.6
    assert ingredients[0].grams_per_piece == 55
    assert ingredients[0].ml_to_g is None


def test_excel_semicolon_decimal_comma_cp1250():
    data = (
        HEADER.replace(",", ";")
        + "\nleca;Leća;mahunarke;Povrće;dry;352;26;60;0;;;green;;\n"
        + "mlijeko;Mlijeko 2,8 %;mliječni;Mliječni proizvodi;ready;60;3,3;4,8;2,8;;1,03;green;;\n"
        + ";;;;;;;;;;;;;\n"
    )
    ingredients, warnings = parse_ingredients(data.encode("cp1250"))
    assert len(warnings) == 1 and "isn't saved as UTF-8" in warnings[0]
    assert [i.name for i in ingredients] == ["Leća", "Mlijeko 2,8 %"]
    assert ingredients[1].protein == 3.3
    assert ingredients[1].ml_to_g == 1.03


def test_broken_lines_are_skipped_with_warning():
    data = (
        f"{HEADER}\n"
        "ok,OK,x,Proteini,raw,100,10,0,5,,,green,,\n"
        "bad,Bad,x,Proteini,raw,abc,10,0,5,,,green,,\n"
        "ok,Again,x,Proteini,raw,100,10,0,5,,,green,,\n"
        "long,Long,x,Proteini,raw,100,10,0,5,,,green,,,extra\n"
    )
    ingredients, warnings = parse_ingredients(data.encode("utf-8"))
    assert [i.id for i in ingredients] == ["ok"]
    assert len(warnings) == 3
    assert "Line 3 (Bad): not a number in kcal" in warnings[0]
    assert "used twice" in warnings[1]
    assert "too many values" in warnings[2]


def test_missing_column():
    ingredients, warnings = parse_ingredients(b"id,name,kcal\nx,X,1\n")
    assert ingredients == []
    assert "missing column" in warnings[0]


def test_utf8_bom_from_excel():
    data = f"{HEADER}\nleca,Leća,x,Povrće,dry,352,26,60,0,,,green,,\n"
    ingredients, warnings = parse_ingredients(data.encode("utf-8-sig"))
    assert warnings == []
    assert ingredients[0].name == "Leća"


RECIPE_HEADER = "recipe_id,recipe,meal,person,ingredient_id,qty,unit,link,method"


def test_recipes_grouped_in_file_order():
    data = (
        f"{RECIPE_HEADER}\n"
        'r1,"Piletina, riža",Ručak,Frane,piletina,200,g,http://x,Korak 1 | Korak 2\n'
        'r1,"Piletina, riža",Ručak,Marinela,piletina,150,g,,\n'
        "r1,,Ručak,Frane,riza,50,G,,\n"
        "r0,Kebab,Ručak,,tortilja,,,,\n"
        "r0,Kebab,Ručak,,sol,2,tbs,,\n"
    )
    recipes, warnings = parse_recipes(
        data.encode("utf-8"), {"piletina", "riza", "tortilja", "sol"}
    )
    assert warnings == []
    assert [r.id for r in recipes] == ["r1", "r0"]
    r1 = recipes[0]
    assert r1.name == "Piletina, riža" and r1.link == "http://x"
    assert r1.method == ["Korak 1", "Korak 2"]
    assert [(i.person, i.qty, i.unit) for i in r1.items] == [
        ("Frane", 200, "g"),
        ("Marinela", 150, "g"),
        ("Frane", 50, "g"),
    ]
    assert recipes[1].items[0].qty is None  # to taste
    assert recipes[1].items[1].unit == "žlica"


def test_recipe_bad_lines_are_skipped():
    data = (
        f"{RECIPE_HEADER}\n"
        "r1,X,Ručak,Frane,nepoznato,100,g,,\n"
        "r1,X,Ručak,Frane,riza,puno,g,,\n"
        "r1,X,Ručak,Frane,riza,1,šalica,,\n"
        "r1,X,Ručak,Frane,riza,50,g,,\n"
    )
    recipes, warnings = parse_recipes(data.encode("utf-8"), {"riza"})
    assert len(recipes[0].items) == 1
    assert "not in ingredients.csv" in warnings[0]
    assert "not a number" in warnings[1]
    assert "unknown unit" in warnings[2]
