from service.routers.nutrition import parse_ingredients

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
