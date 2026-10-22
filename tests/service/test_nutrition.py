import pytest
from fastapi import HTTPException

from service.routers.nutrition import (
    RecipeIn,
    RecipeItem,
    change_recipe,
    file_version,
    next_recipe_id,
    parse_ingredients,
    parse_recipes,
    recipe_ids,
)

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


# ---- saving recipes from the page ----

INGREDIENTS = (
    f"{HEADER}\n"
    "piletina,Piletina,meso,Proteini,raw,120,23,0,2.6,,,green,,\n"
    "riza,Riža,žitarice,Ugljikohidrati,dry,360,7,79,0.6,,,green,,\n"
    "jaje,Jaje,jaja,Proteini,raw,143,12.6,0.7,9.5,55,,green,,\n"
)
RECIPES = (
    f"{RECIPE_HEADER}\r\n"
    'rucak-1,"Piletina, riža",Ručak,Frane,piletina,200,g,http://x,Korak 1 | Korak 2\r\n'
    'rucak-1,"Piletina, riža",Ručak,Frane,riza,50,g,,\r\n'
    "snack-1,Jaja,Snack,Frane,jaje,2,kom,,\r\n"
    "broken,line,with,too,many,values,1,g,,,,extra\r\n"
    "rucak-2,Riža,Ručak,Marinela,riza,60,g,,\r\n"
)


@pytest.fixture
def files(tmp_path):
    ing, rec = tmp_path / "ingredients.csv", tmp_path / "recipes.csv"
    ing.write_text(INGREDIENTS, encoding="utf-8")
    rec.write_bytes(RECIPES.encode("utf-8-sig"))
    return rec, ing


def _recipe(version, name="Piletina, riža", meal="Ručak", items=None, **kw):
    items = items or [
        RecipeItem(person="Marinela", ingredient_id="piletina", qty=150, unit="g")
    ]
    return RecipeIn(name=name, meal=meal, items=items, version=version, **kw)


def _version(rec):
    return file_version(rec.read_bytes())


def test_edit_replaces_only_that_recipe(files):
    rec, ing = files
    items = [
        RecipeItem(person="Frane", ingredient_id="piletina", qty=180, unit="g"),
        RecipeItem(person="Marinela", ingredient_id="piletina", qty=150, unit="g"),
        RecipeItem(person="", ingredient_id="riza", qty=None, unit=""),
    ]
    v = _version(rec)
    recipe = _recipe(v, items=items, link="http://x", method=["Korak 1", "Korak 2"])
    saved = change_recipe(rec, ing, "rucak-1", recipe, v)
    assert saved.id == "rucak-1" and saved.version == _version(rec)

    raw = rec.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM kept
    lines = raw.decode("utf-8-sig").split("\r\n")  # line endings kept
    old = RECIPES.split("\r\n")
    assert lines[0] == old[0]
    assert lines[1] == (
        'rucak-1,"Piletina, riža",Ručak,Frane,piletina,180,g,http://x,Korak 1 | Korak 2'
    )
    assert lines[2] == 'rucak-1,"Piletina, riža",Ručak,Marinela,piletina,150,g,,'
    assert lines[3] == 'rucak-1,"Piletina, riža",Ručak,,riza,,,,'
    assert lines[4:] == old[3:]  # every other line byte for byte, broken one too

    recipes, _ = parse_recipes(raw, {"piletina", "riza", "jaje"})
    r = recipes[0]
    assert r.name == "Piletina, riža" and r.method == ["Korak 1", "Korak 2"]
    assert [(i.person, i.qty) for i in r.items] == [
        ("Frane", 180),
        ("Marinela", 150),
        ("", None),
    ]


def test_create_appends_with_next_id(files):
    rec, ing = files
    v = _version(rec)
    saved = change_recipe(rec, ing, None, _recipe(v, name="Nova"), v)
    assert saved.id == "rucak-3"
    text = rec.read_bytes().decode("utf-8-sig")
    assert text.endswith("rucak-3,Nova,Ručak,Marinela,piletina,150,g,,\r\n")
    assert recipe_ids(rec.read_bytes())[-1] == "rucak-3"


def test_delete(files):
    rec, ing = files
    change_recipe(rec, ing, "rucak-1", None, _version(rec))
    assert recipe_ids(rec.read_bytes()) == ["snack-1", "broken", "rucak-2"]


def test_changed_file_is_refused(files):
    rec, ing = files
    old = _version(rec)
    rec.write_bytes(rec.read_bytes() + b"snack-2,X,Snack,Frane,jaje,1,kom,,\r\n")
    with pytest.raises(HTTPException) as e:
        change_recipe(rec, ing, "rucak-1", _recipe(old), old)
    assert e.value.status_code == 409


def test_unknown_ingredient_is_refused(files):
    rec, ing = files
    v = _version(rec)
    bad = [RecipeItem(person="Frane", ingredient_id="nepoznato", qty=1, unit="g")]
    with pytest.raises(HTTPException) as e:
        change_recipe(rec, ing, "rucak-1", _recipe(v, items=bad), v)
    assert e.value.status_code == 422 and "nepoznato" in e.value.detail
    assert rec.read_bytes() == RECIPES.encode("utf-8-sig")  # untouched


def test_semicolon_file(tmp_path):
    ing, rec = tmp_path / "ingredients.csv", tmp_path / "recipes.csv"
    ing.write_text(INGREDIENTS, encoding="utf-8")
    header = RECIPE_HEADER.replace(",", ";")
    rec.write_text(f"{header}\nr1;Jaja;Snack;Frane;jaje;2;kom;;\n", encoding="utf-8")
    items = [RecipeItem(person="Frane", ingredient_id="jaje", qty=2.5, unit="KOM")]
    v = _version(rec)
    change_recipe(rec, ing, "r1", _recipe(v, "Jaja, 2", "Snack", items), v)
    lines = rec.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "r1;Jaja, 2;Snack;Frane;jaje;2.5;kom;;"


def test_next_recipe_id():
    assert next_recipe_id("Ručak", ["rucak-1", "rucak-9", "snack-2"]) == "rucak-10"
    assert next_recipe_id("Doručak", []) == "dorucak-1"
    assert next_recipe_id("Ostalo", ["recept-1"]) == "recept-2"
