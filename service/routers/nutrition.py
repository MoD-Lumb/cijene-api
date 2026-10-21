import csv
import logging
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from service.routers.auth import RequireAuth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Nutrition"], dependencies=[RequireAuth])

NUTRITION_DIR = Path(__file__).resolve().parents[2] / "nutrition"
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
    recipes, warnings = parse_recipes(
        RECIPES_CSV.read_bytes(), {i.id for i in ingredients}
    )
    for w in warnings:
        logger.warning(f"recipes.csv: {w}")
    return ListRecipesResponse(recipes=recipes, warnings=warnings)
