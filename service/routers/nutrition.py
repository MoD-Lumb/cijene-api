import csv
import logging
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from service.routers.auth import RequireAuth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Nutrition"], dependencies=[RequireAuth])

INGREDIENTS_CSV = Path(__file__).resolve().parents[2] / "nutrition" / "ingredients.csv"

NUMBER_FIELDS = ["kcal", "protein", "carbs", "fat"]
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


def _decode(data: bytes) -> str:
    # UTF-8 from Notepad/VS Code; Excel "CSV (comma delimited)" on Croatian
    # Windows saves Windows-1250 instead.
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1250")


def _number(value: str) -> float:
    # Accept the Croatian decimal comma (12,5) as well as 12.5.
    return float(value.strip().replace(" ", "").replace(",", "."))


def parse_ingredients(data: bytes) -> tuple[list[IngredientResponse], list[str]]:
    """
    Parse the ingredients CSV, forgiving the usual Excel differences.

    Accepts "," or ";" as separator (Excel with Croatian settings uses ";")
    and decimal commas. A broken line is skipped and reported in the
    returned warnings instead of failing the whole list.
    """
    text = _decode(data)
    lines = text.splitlines()
    if not lines:
        return [], ["The file is empty."]

    header = lines[0]
    delimiter = ";" if header.count(";") > header.count(",") else ","
    reader = csv.DictReader(lines, delimiter=delimiter)
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames or []]

    required = ["id", "name", *NUMBER_FIELDS]
    missing = [f for f in required if f not in reader.fieldnames]
    if missing:
        return [], [f"Header line is missing column(s): {', '.join(missing)}."]

    ingredients: list[IngredientResponse] = []
    warnings: list[str] = []
    seen: set[str] = set()

    # Line 1 is the header, so data starts at line 2.
    for line_no, row in enumerate(reader, start=2):
        r = {k: (v or "").strip() for k, v in row.items() if k}
        if not any(r.values()):
            continue  # empty line, e.g. at the end of an Excel file

        label = f"Line {line_no} ({r.get('name') or r.get('id') or '?'})"
        if None in row:
            warnings.append(
                f"{label}: too many values (a comma inside a name?), skipped."
            )
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
