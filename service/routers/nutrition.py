import codecs
import csv
import hashlib
import io
import logging
import os
import re
import unicodedata
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from service.routers.auth import RequireAuth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Nutrition"], dependencies=[RequireAuth])

# The repo's nutrition/ folder; NUTRITION_DIR points a test instance at a copy.
NUTRITION_DIR = Path(
    os.getenv("NUTRITION_DIR", str(Path(__file__).resolve().parents[2] / "nutrition"))
)
INGREDIENTS_CSV = NUTRITION_DIR / "ingredients.csv"
RECIPES_CSV = NUTRITION_DIR / "recipes.csv"

# Recipe units; spelling variants people (and Excel) use map to one name.
UNITS = {
    "g": "g",
    "kom": "kom",
    "ml": "ml",
    "dl": "dl",
    "žlica": "žlica",
    "zlica": "žlica",
    "žlice": "žlica",
    "tbs": "žlica",
    "tbsp": "žlica",
}

NUMBER_FIELDS = ["kcal", "protein", "carbs", "fat"]
TOO_MANY = "too many values (a comma inside a name?), skipped."
OPTIONAL_NUMBER_FIELDS = ["grams_per_piece", "ml_to_g"]


class IngredientResponse(BaseModel):
    """Ingredient with macros per 100 g."""

    id: str = Field(..., description="Stable ingredient id (slug).")
    name: str = Field(..., description="Ingredient name.")
    food_group: str = Field(..., description="Food group, e.g. meso, žitarice.")
    meal_role: str = Field(..., description="Meal slot it fills, e.g. Proteini.")
    state: str = Field(..., description="raw, dry or ready: how it is weighed.")
    kcal: float = Field(..., description="kcal per 100 g.")
    protein: float = Field(..., description="Protein (g) per 100 g.")
    carbs: float = Field(..., description="Carbohydrates (g) per 100 g.")
    fat: float = Field(..., description="Fat (g) per 100 g.")
    grams_per_piece: float | None = Field(..., description="Weight of one piece.")
    ml_to_g: float | None = Field(..., description="Grams per ml for liquids.")
    preference: str = Field(..., description="Default preference: green/yellow/red.")
    source: str = Field(..., description="Where the values come from.")
    note: str = Field(..., description="Changes and things to check.")


class ListIngredientsResponse(BaseModel):
    """List ingredients response schema."""

    ingredients: list[IngredientResponse] = Field(..., description="All ingredients.")
    warnings: list[str] = Field(
        ..., description="Lines that were skipped or fixed, with the reason."
    )


def _decode(data: bytes) -> tuple[str, str | None]:
    # UTF-8 from Notepad/VS Code or Excel's "CSV UTF-8". Excel's plain
    # "CSV (Comma delimited)" saves the Windows ANSI code page instead, which
    # can't hold every Croatian letter (č, ć and đ become "?").
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return data.decode("cp1250"), (
            "The file isn't saved as UTF-8, so č/ć/đ may have turned into '?'. "
            "In Excel use Save As → 'CSV UTF-8 (Comma delimited)'."
        )


def _number(value: str) -> float:
    # Accept the Croatian decimal comma (12,5) as well as 12.5.
    return float(value.strip().replace(" ", "").replace(",", "."))


def _csv_rows(
    data: bytes, required: list[str]
) -> tuple[list[tuple[str, dict[str, str], bool]], list[str]]:
    """
    Read a CSV file, forgiving the usual Excel differences.

    Accepts "," or ";" as separator (Excel with Croatian settings uses ";").
    Returns the non-empty rows as (label, values, too_many_values), with a
    label like "Line 5 (Riža)" for warnings, plus the warnings found so far.
    """
    text, encoding_warning = _decode(data)
    lines = text.splitlines()
    if not lines:
        return [], ["The file is empty."]

    header = lines[0]
    delimiter = ";" if header.count(";") > header.count(",") else ","
    reader = csv.DictReader(lines, delimiter=delimiter)
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames or []]

    missing = [f for f in required if f not in reader.fieldnames]
    if missing:
        return [], [f"Header line is missing column(s): {', '.join(missing)}."]

    rows: list[tuple[str, dict[str, str], bool]] = []
    warnings: list[str] = [encoding_warning] if encoding_warning else []

    # Line 1 is the header, so data starts at line 2.
    for line_no, row in enumerate(reader, start=2):
        r = {k: (v or "").strip() for k, v in row.items() if k}
        if not any(r.values()):
            continue  # empty line, e.g. at the end of an Excel file

        name = r.get("name") or r.get("recipe") or r.get("id") or "?"
        rows.append((f"Line {line_no} ({name})", r, None in row))

    return rows, warnings


def parse_ingredients(data: bytes) -> tuple[list[IngredientResponse], list[str]]:
    """
    Parse the ingredients CSV.

    Accepts decimal commas. A broken line is skipped and reported in the
    returned warnings instead of failing the whole list.
    """
    rows, warnings = _csv_rows(data, ["id", "name", *NUMBER_FIELDS])
    ingredients: list[IngredientResponse] = []
    seen: set[str] = set()

    for label, r, too_many in rows:
        if too_many:
            warnings.append(f"{label}: {TOO_MANY}")
            continue
        if not r.get("id") or not r.get("name"):
            warnings.append(f"{label}: id or name is empty, skipped.")
            continue
        if r["id"] in seen:
            warnings.append(f"{label}: id '{r['id']}' is used twice, skipped.")
            continue

        try:
            numbers = {f: _number(r[f]) for f in NUMBER_FIELDS}
        except ValueError:
            bad = [f for f in NUMBER_FIELDS if not _is_number(r.get(f, ""))]
            warnings.append(f"{label}: not a number in {', '.join(bad)}, skipped.")
            continue

        optional: dict[str, float | None] = {}
        for f in OPTIONAL_NUMBER_FIELDS:
            value = r.get(f, "")
            if not value:
                optional[f] = None
            elif _is_number(value):
                optional[f] = _number(value)
            else:
                optional[f] = None
                warnings.append(f"{label}: '{value}' in {f} is not a number, ignored.")

        seen.add(r["id"])
        ingredients.append(
            IngredientResponse(
                id=r["id"],
                name=r["name"],
                food_group=r.get("food_group", ""),
                meal_role=r.get("meal_role", "") or "Ostalo",
                state=r.get("state", ""),
                **numbers,
                **optional,
                preference=r.get("preference", "") or "green",
                source=r.get("source", ""),
                note=r.get("note", ""),
            )
        )

    return ingredients, warnings


class RecipeItem(BaseModel):
    """One ingredient of a recipe, for one person (or the whole batch)."""

    person: str = Field(..., description="Username, or empty for the whole batch.")
    ingredient_id: str = Field(..., description="Id from ingredients.csv.")
    qty: float | None = Field(..., description="Quantity, or null for 'to taste'.")
    unit: str = Field(..., description="g, kom, ml, dl or žlica; empty = to taste.")


class RecipeResponse(BaseModel):
    """A recipe with per-person quantities."""

    id: str = Field(..., description="Stable recipe id.")
    name: str = Field(..., description="Recipe name.")
    meal: str = Field(..., description="Doručak, Snack, Ručak or Večera.")
    link: str = Field(..., description="Link to the original recipe, if any.")
    method: list[str] = Field(..., description="Preparation steps.")
    items: list[RecipeItem] = Field(..., description="Ingredients per person.")


class ListRecipesResponse(BaseModel):
    """List recipes response schema."""

    recipes: list[RecipeResponse] = Field(..., description="All recipes, file order.")
    warnings: list[str] = Field(
        ..., description="Lines that were skipped or fixed, with the reason."
    )
    version: str = Field(..., description="Version of recipes.csv, sent back on save.")


class RecipeIn(BaseModel):
    """A recipe to save into recipes.csv."""

    name: str = Field(..., description="Recipe name.")
    meal: str = Field(..., description="Doručak, Snack, Ručak or Večera.")
    link: str = Field("", description="Link to the original recipe.")
    method: list[str] = Field(default_factory=list, description="Preparation steps.")
    items: list[RecipeItem] = Field(..., description="Ingredients per person.")
    version: str = Field(..., description="The version the page loaded.")


class SavedRecipeResponse(BaseModel):
    """Result of saving or deleting a recipe."""

    id: str = Field(..., description="Recipe id.")
    version: str = Field(..., description="New version of recipes.csv.")


def parse_recipes(
    data: bytes, ingredient_ids: set[str]
) -> tuple[list[RecipeResponse], list[str]]:
    """
    Parse the recipes CSV: one line per ingredient per person.

    Lines of the same recipe_id are grouped, keeping the file order. Link and
    method can be on any line of the recipe (the first non-empty one wins);
    method steps are separated by "|".
    """
    rows, warnings = _csv_rows(data, ["recipe_id", "ingredient_id", "qty", "unit"])
    recipes: dict[str, RecipeResponse] = {}

    for label, r, too_many in rows:
        if too_many:
            warnings.append(f"{label}: {TOO_MANY}")
            continue
        rid, ing = r.get("recipe_id", ""), r.get("ingredient_id", "")
        if not rid:
            warnings.append(f"{label}: recipe_id is empty, skipped.")
            continue

        recipe = recipes.get(rid)
        if recipe is None:
            recipe = recipes[rid] = RecipeResponse(
                id=rid,
                name=r.get("recipe") or rid,
                meal=r.get("meal") or "Ostalo",
                link="",
                method=[],
                items=[],
            )
        if not recipe.link and r.get("link"):
            recipe.link = r["link"]
        if not recipe.method and r.get("method"):
            recipe.method = [s.strip() for s in r["method"].split("|") if s.strip()]

        if ing not in ingredient_ids:
            warnings.append(
                f"{label}: ingredient '{ing}' is not in ingredients.csv, skipped."
            )
            continue

        qty_text, unit_text = r.get("qty", ""), r.get("unit", "").lower()
        if not qty_text:
            qty, unit = None, ""  # "to taste"
        elif not _is_number(qty_text):
            warnings.append(f"{label}: qty '{qty_text}' is not a number, skipped.")
            continue
        elif unit_text not in UNITS:
            warnings.append(
                f"{label}: unknown unit '{unit_text}' "
                f"(use g, kom, ml, dl or žlica), skipped."
            )
            continue
        else:
            qty, unit = _number(qty_text), UNITS[unit_text]

        recipe.items.append(
            RecipeItem(
                person=r.get("person", ""), ingredient_id=ing, qty=qty, unit=unit
            )
        )

    return list(recipes.values()), warnings


def _is_number(value: str) -> bool:
    try:
        _number(value)
        return True
    except ValueError:
        return False


def file_version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _split_lines(data: bytes) -> tuple[list[str], str, str, bool, bool]:
    """Raw lines of a CSV file plus its delimiter, line ending, BOM and final newline."""
    text = data.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    final_newline = lines[-1] == ""
    if final_newline:
        lines.pop()
    header = lines[0] if lines else ""
    delimiter = ";" if header.count(";") > header.count(",") else ","
    return lines, delimiter, newline, data.startswith(codecs.BOM_UTF8), final_newline


def _line_recipe_id(line: str, delimiter: str, column: int) -> str:
    values = next(csv.reader([line], delimiter=delimiter), [])
    return values[column].strip() if len(values) > column else ""


def recipe_ids(data: bytes) -> list[str]:
    """Recipe ids in file order, read from the raw lines (also broken ones)."""
    lines, delimiter, *_ = _split_lines(data)
    header = next(csv.reader([lines[0]], delimiter=delimiter), []) if lines else []
    fields = [f.strip().lower() for f in header]
    if "recipe_id" not in fields:
        return []
    column = fields.index("recipe_id")
    ids = [_line_recipe_id(line, delimiter, column) for line in lines[1:]]
    return list(dict.fromkeys(i for i in ids if i))


def write_recipe(
    data: bytes, recipe_id: str, new_lines: list[dict[str, str]] | None
) -> bytes:
    """
    Replace one recipe's lines in recipes.csv, add them at the end if it's new,
    or remove them (new_lines=None).

    Works on the raw lines: every other line stays byte for byte as it was,
    so `git diff` shows only this recipe. Keeps the BOM, separator and line
    endings of the file.
    """
    lines, delimiter, newline, bom, final_newline = _split_lines(data)
    fields = [
        f.strip().lower() for f in next(csv.reader([lines[0]], delimiter=delimiter))
    ]
    column = fields.index("recipe_id")

    written = []
    for values in new_lines or []:
        buf = io.StringIO()
        csv.writer(buf, delimiter=delimiter, lineterminator="").writerow(
            [values.get(f, "") for f in fields]
        )
        written.append(buf.getvalue())

    out, placed = [lines[0]], False
    for line in lines[1:]:
        if _line_recipe_id(line, delimiter, column) == recipe_id:
            if not placed:
                out.extend(written)  # in place of the recipe's first line
                placed = True
            continue
        out.append(line)
    if not placed:
        out.extend(written)

    text = newline.join(out) + (newline if final_newline else "")
    return (codecs.BOM_UTF8 if bom else b"") + text.encode("utf-8")


def _qty_text(qty: float) -> str:
    return str(int(qty)) if qty == int(qty) else str(round(qty, 3))


def recipe_lines(recipe_id: str, recipe: RecipeIn) -> list[dict[str, str]]:
    """CSV lines of a recipe: link and method only on its first line."""
    return [
        {
            "recipe_id": recipe_id,
            "recipe": recipe.name,
            "meal": recipe.meal,
            "person": item.person,
            "ingredient_id": item.ingredient_id,
            "qty": "" if item.qty is None else _qty_text(item.qty),
            "unit": "" if item.qty is None else item.unit,
            "link": recipe.link if n == 0 else "",
            "method": " | ".join(recipe.method) if n == 0 else "",
        }
        for n, item in enumerate(recipe.items)
    ]


def _slug(text: str) -> str:
    plain = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def next_recipe_id(meal: str, existing: list[str]) -> str:
    """Meal slug + next free number, like the ids in the file: rucak-10, snack-5."""
    prefix = _slug(meal) if meal and meal != "Ostalo" else "recept"
    prefix = prefix or "recept"
    numbers = [
        int(m.group(1)) for i in existing if (m := re.fullmatch(rf"{prefix}-(\d+)", i))
    ]
    return f"{prefix}-{max(numbers, default=0) + 1}"


def clean_recipe(recipe: RecipeIn, ingredient_ids: set[str]) -> RecipeIn:
    """Check a recipe from the page; raises 422 with a readable reason."""

    def bad(reason: str) -> HTTPException:
        return HTTPException(status_code=422, detail=reason)

    name = " ".join(recipe.name.split())
    if not name:
        raise bad("The recipe needs a name.")
    if not recipe.items:
        raise bad("Add at least one ingredient.")
    items = []
    for item in recipe.items:
        if item.ingredient_id not in ingredient_ids:
            raise bad(f"Ingredient '{item.ingredient_id}' is not in ingredients.csv.")
        unit = UNITS.get(item.unit.strip().lower(), "")
        if item.qty is not None:
            if item.qty < 0:
                raise bad("Quantities can't be negative.")
            if not unit:
                raise bad(f"Unknown unit '{item.unit}' (use g, kom, ml, dl or žlica).")
        items.append(
            RecipeItem(
                person=item.person.strip(),
                ingredient_id=item.ingredient_id,
                qty=item.qty,
                unit=unit if item.qty is not None else "",
            )
        )
    return RecipeIn(
        name=name,
        meal=recipe.meal.strip() or "Ostalo",
        link=recipe.link.strip(),
        method=[s.strip().replace("|", "/") for s in recipe.method if s.strip()],
        items=items,
        version=recipe.version,
    )


def change_recipe(
    recipes_csv: Path,
    ingredients_csv: Path,
    recipe_id: str | None,
    recipe: RecipeIn | None,
    version: str,
) -> SavedRecipeResponse:
    """
    Add (recipe_id None), replace or delete (recipe None) one recipe in
    recipes.csv, after checking nobody changed the file in the meantime.
    """
    data = recipes_csv.read_bytes()
    if file_version(data) != version:
        raise HTTPException(
            status_code=409,
            detail="recipes.csv changed since this page loaded it (edited in "
            "Notepad, or saved from another tab). Copy your changes, reload the "
            "page and try again.",
        )
    if _decode(data)[1]:
        raise HTTPException(
            status_code=409,
            detail="recipes.csv isn't saved as UTF-8, so the page won't write "
            "into it. Open it in Notepad, choose Save As → Encoding UTF-8, "
            "then reload the page.",
        )

    existing = recipe_ids(data)
    if recipe_id is not None and recipe_id not in existing:
        raise HTTPException(status_code=404, detail=f"No recipe '{recipe_id}'.")
    lines = None
    if recipe is not None:
        ingredients, _ = parse_ingredients(ingredients_csv.read_bytes())
        recipe = clean_recipe(recipe, {i.id for i in ingredients})
        recipe_id = recipe_id or next_recipe_id(recipe.meal, existing)
        lines = recipe_lines(recipe_id, recipe)
    assert recipe_id is not None

    new = write_recipe(data, recipe_id, lines)
    tmp = recipes_csv.with_name(recipes_csv.name + ".tmp")
    try:
        tmp.write_bytes(new)
        tmp.replace(recipes_csv)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail="Couldn't write recipes.csv. Is it open in Excel? "
            "Close it there and save again.",
        )
    return SavedRecipeResponse(id=recipe_id, version=file_version(new))


@router.get("/ingredients/", summary="List ingredients with macros")
async def list_ingredients() -> ListIngredientsResponse:
    """
    List all ingredients from nutrition/ingredients.csv.

    The file is read on every request, so edits show up without a restart.
    Broken lines are skipped and listed in `warnings`.
    """
    ingredients, warnings = parse_ingredients(INGREDIENTS_CSV.read_bytes())
    for w in warnings:
        logger.warning(f"ingredients.csv: {w}")
    return ListIngredientsResponse(ingredients=ingredients, warnings=warnings)


@router.get("/recipes/", summary="List recipes with per-person quantities")
async def list_recipes() -> ListRecipesResponse:
    """
    List all recipes from nutrition/recipes.csv.

    Macros aren't included: combine the quantities with /v1/ingredients/.
    The file is read on every request; broken lines are listed in `warnings`.
    """
    ingredients, _ = parse_ingredients(INGREDIENTS_CSV.read_bytes())
    data = RECIPES_CSV.read_bytes()
    recipes, warnings = parse_recipes(data, {i.id for i in ingredients})
    for w in warnings:
        logger.warning(f"recipes.csv: {w}")
    return ListRecipesResponse(
        recipes=recipes, warnings=warnings, version=file_version(data)
    )


@router.post("/recipes/", summary="Add a recipe to recipes.csv")
async def create_recipe(recipe: RecipeIn) -> SavedRecipeResponse:
    """
    Add a recipe at the end of nutrition/recipes.csv. The id is the meal plus
    the next free number (e.g. rucak-10). `version` must be the one from
    GET /v1/recipes/, otherwise 409.
    """
    return change_recipe(RECIPES_CSV, INGREDIENTS_CSV, None, recipe, recipe.version)


@router.put("/recipes/{recipe_id}", summary="Replace a recipe in recipes.csv")
async def update_recipe(recipe_id: str, recipe: RecipeIn) -> SavedRecipeResponse:
    """
    Replace the lines of one recipe in nutrition/recipes.csv, in place.
    All other lines stay exactly as they were.
    """
    return change_recipe(
        RECIPES_CSV, INGREDIENTS_CSV, recipe_id, recipe, recipe.version
    )


@router.delete("/recipes/{recipe_id}", summary="Delete a recipe from recipes.csv")
async def delete_recipe(recipe_id: str, version: str) -> SavedRecipeResponse:
    """Remove all lines of one recipe from nutrition/recipes.csv."""
    return change_recipe(RECIPES_CSV, INGREDIENTS_CSV, recipe_id, None, version)
